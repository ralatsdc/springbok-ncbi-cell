# https://chanzuckerberg.github.io/cellxgene-census/notebooks/analysis_demo/comp_bio_explore_and_load_lung_data.html
# http://localhost:8889/notebooks/python_raw/get_dataset.ipynb
import logging
from multiprocessing.pool import Pool
import os
import pickle
import re
import requests
import subprocess
from time import sleep
from urllib import parse

from bs4 import BeautifulSoup
import cellxgene_census
import pandas as pd

import numpy as np
import scanpy as sc
import matplotlib.pyplot as plt
import nsforest as ns
from nsforest import utils
from nsforest import preprocessing as pp
from nsforest import nsforesting
from nsforest import evaluating as ev
from nsforest import plotting as pl


EUTILS_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
EMAIL = "raymond.leclair@gmail.com"
NCBI_API_KEY = os.environ.get("NCBI_API_KEY")
NCBI_API_SLEEP = 1
PUBMED = "pubmed"
PUBMEDCENTRAL = "pmc"

ONTOGPT_DIR = "ontogpt"

CELLXGENE_DOMAIN_NAME = "cellxgene.cziscience.com"
CELLXGENE_API_URL_BASE = f"https://api.{CELLXGENE_DOMAIN_NAME}"
CELLXGENE_DIR = "cellxgene"


def get_lung_obs_and_datasets():

    lung_obs_parquet = "lung_obs.parquet"
    lung_datasets_parquet = "lung_datasets.parquet"

    if not os.path.exists(lung_obs_parquet) or not os.path.exists(
        lung_datasets_parquet
    ):

        print("Opening soma")
        census = cellxgene_census.open_soma(census_version="latest")

        print("Collecting all datasets")
        datasets = census["census_info"]["datasets"].read().concat().to_pandas()

        print("Collecting lung obs")
        lung_obs = (
            census["census_data"]["homo_sapiens"]
            .obs.read(
                value_filter="tissue_general == 'lung' and is_primary_data == True"
            )
            .concat()
            .to_pandas()
        )

        census.close()

        print("Write lung obs parquet")
        lung_obs.to_parquet(lung_obs_parquet)

        print("Finding lung datasets")
        lung_datasets = datasets[datasets["dataset_id"].isin(lung_obs["dataset_id"])]

        print("Write lung datasets parquet")
        lung_datasets.to_parquet(lung_datasets_parquet)

    else:

        print("Read lung obs parquet")
        lung_obs = pd.read_parquet(lung_obs_parquet)

        print("Read lung datasets parquet")
        lung_datasets = pd.read_parquet(lung_datasets_parquet)

    return lung_obs, lung_datasets


def get_title(citation):

    citation_url = None
    title = None

    p1 = re.compile("Publication: (.*) Dataset Version:")
    p2 = re.compile("articleName : '(.*)',")

    selectors = [
        "h1.c-article-title",
        "h1.article-header__title.smaller",
        "div.core-container h1",
        "h1.content-header__title.content-header__title--xx-long",
        "h1#page-title.highwire-cite-title",
    ]

    m1 = p1.search(citation)
    if not m1:
        logging.warning(f"Could not find citation URL for {citation}")
        return citation_url, title

    citation_url = m1.group(1)

    print(f"Getting title for citation URL: {citation_url}")

    response = requests.get(citation_url)

    try_wget = True
    if response.status_code == 200:
        html_data = response.text

        fullsoup = BeautifulSoup(html_data, features="lxml")
        for selector in selectors:
            selected = fullsoup.select(selector)
            if selected:
                if len(selected) > 1:
                    logging.warning(
                        f"Selected more than one element using {selector} on soup from {citation_url}"
                    )
                title = selected[0].text
                try_wget = False
                break

    if try_wget:

        completed_process = subprocess.run(
            ["curl", "-L", citation_url], capture_output=True
        )

        html_data = completed_process.stdout
        fullsoup = BeautifulSoup(html_data, features="lxml")

        found = fullsoup.find_all("script")
        if found and len(found) > 4:
            m2 = p2.search(found[4].text)
            if m2:
                title = m2.group(1)

    print(f"Found title: '{title}' for citation URL: {citation_url}")

    return title


def get_titles(lung_datasets):

    titles = []

    titles_pickle = "titles.pickle"

    if not os.path.exists(titles_pickle):

        print("Getting titles")
        citations = [c for c in lung_datasets.citation]
        with Pool(8) as p:
            titles = p.map(get_title, citations)
        titles = list(set([title for title in titles if title is not None]))

        print("Dumping titles")
        with open(titles_pickle, "wb") as f:
            pickle.dump(titles, f, pickle.HIGHEST_PROTOCOL)

    else:

        print("Loading titles")
        with open(titles_pickle, "rb") as f:
            titles = pickle.load(f)

    return titles


def get_pmid_for_title(title):

    print(f"Getting PMID for title: '{title}'")

    pmid = None

    search_url = EUTILS_URL + "esearch.fcgi"

    params = {
        "db": PUBMED,
        "term": title,
        "field": "title",
        "retmode": "json",
        # "retmax": 0,
        "email": EMAIL,
        "api_key": NCBI_API_KEY,
    }

    sleep(NCBI_API_SLEEP)

    response = requests.get(search_url, params=parse.urlencode(params, safe=","))

    if response.status_code == 200:
        data = response.json()

        resultcount = int(data["esearchresult"]["count"])
        if resultcount > 1:
            logging.warning(f"PubMed returned more than one result for title: {title}")
            for _pmid in data["esearchresult"]["idlist"]:
                _title = get_title_for_pmid(_pmid)
                if (
                    _title == title + "."
                ):  # PubMedCentral includes period in title, PubMed does not
                    pmid = _pmid
                    print(f"Found PMID: {pmid} for title: '{title}'")
                    break

            if not pmid:
                pmid = data["esearchresult"]["idlist"][0]
                print(f"Using first PMID: {pmid} for title '{title}'")

        else:
            pmid = data["esearchresult"]["idlist"][0]
            print(f"Found PMID: {pmid} for title: '{title}'")

    elif response.status_code == 429:
        logging.error("Too many requests to NCBI API. Try again later, or use API key.")

    else:
        logging.error("Encountered error in searching PubMed: {response.status_code}")

    return pmid


def get_title_for_pmid(pmid):

    title = None

    fetch_url = EUTILS_URL + "efetch.fcgi"

    params = {
        "db": PUBMED,
        "id": pmid,
        "rettype": "xml",
        "email": EMAIL,
        "api_key": NCBI_API_KEY,
    }

    sleep(NCBI_API_SLEEP)

    response = requests.get(fetch_url, params=parse.urlencode(params, safe=","))

    if response.status_code == 200:
        xml_data = response.text

        fullsoup = BeautifulSoup(xml_data, "xml")
        found = fullsoup.find("ArticleTitle")
        if found:
            title = found.text

    else:
        logging.error(
            f"Encountered error in fetching from PubMed: {response.status_code}"
        )

    return title


def get_pmids(titles):

    pmids = []

    pmids_pickle = "pmids.pickle"

    if not os.path.exists(pmids_pickle):

        print("Getting PMIDs")
        with Pool(8) as p:
            pmids = p.map(get_pmid_for_title, titles)
        pmids = list(set([pmid for pmid in pmids if pmid is not None]))

        print("Dumping PMIDs")
        with open(pmids_pickle, "wb") as f:
            pickle.dump(pmids, f, pickle.HIGHEST_PROTOCOL)

    else:

        print("Loading PMIDs")
        with open(pmids_pickle, "rb") as f:
            pmids = pickle.load(f)

    return pmids


def run_ontogpt_pubmed_annotate(pmid):
    """
    run_ontogpt_pubmed_annotate("38540357")
    """
    output_path = f"{ONTOGPT_DIR}/{pmid}.out"
    if not os.path.exists(output_path):
        print(f"Running ontogpt pubmed-annotate for PMID: {pmid}")

        subprocess.run(
            [
                "ontogpt",
                "pubmed-annotate",
                "--template",
                "cell_type",
                pmid,
                "--limit",
                "1",
                "--output",
                output_path,
            ],
        )

        print(f"Completed ontogpt pubmed-annotate for PMID: {pmid}")

    else:
        print(f"Ontogpt pubmed-annotate output for PMID: {pmid} exists")


def run_ontogpt(pmids):

    with Pool(8) as p:
        p.map(run_ontogpt_pubmed_annotate, pmids)


def get_dataset(dataset_series):

    collection_id = dataset_series.collection_id
    dataset_id = dataset_series.dataset_id

    dataset_url = f"{CELLXGENE_API_URL_BASE}/curation/v1/collections/{collection_id}/datasets/{dataset_id}"

    response = requests.get(dataset_url)
    response.raise_for_status()

    if response.status_code != 200:
        logging.error(f"Could not get dataset for id {dataset_id}")
        return

    data = response.json()
    if dataset_id != data["dataset_id"]:
        logging.error(
            f"Response dataset id: {data['dataset_id']} does not equal specified dataset id: {dataset_id}"
        )
        return

    assets = data["assets"]
    for asset in assets:

        if asset["filetype"] != "H5AD":
            continue

        download_filename = f"{CELLXGENE_DIR}/{dataset_id}.{asset['filetype']}"
        if not os.path.exists(download_filename):

            print(f"Downloading dataset file: {download_filename}")

            with requests.get(asset["url"], stream=True) as response:
                response.raise_for_status()

                with open(download_filename, "wb") as df:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        df.write(chunk)

            print(f"Dataset file: {download_filename} downloaded")

        else:

            print(f"Dataset file: {download_filename} exists")


def abc():

    # === Data Exploration

    # Loading h5ad AnnData file
    data_folder = "../demo_data/"
    file = data_folder + "adata_layer1.h5ad"
    adata = sc.read_h5ad(file)

    # Defining `cluster_header` as cell type annotation.
    #
    # Note: Some datasets have multiple annotations per sample
    # (ex. "broad_cell_type" and "granular_cell_type"). NS-Forest can be
    # run on multiple `cluster_header`'s. Combining the parent and child
    # markers may improve classification results.
    cluster_header = "cluster"

    # Defining `output_folder` for saving results
    output_folder = "../outputs_layer1/"

    # Looking at sample labels
    # adata.obs_names

    # Looking at genes
    #
    # Note: `adata.var_names` must be unique. If there is a problem,
    # usually it can be solved by assigning `adata.var.index =
    # adata.var["ensembl_id"]`.
    # adata.var_names

    # Checking cell annotation sizes
    #
    # Note: Some datasets are too large and need to be downsampled to be
    # run through the pipeline. When downsampling, be sure to have all the
    # granular cluster annotations represented.
    # adata.obs[cluster_header].value_counts()


    # === Preprocessing

    # Generating scanpy dendrogram
    #
    # Note: Only run if there is no pre-defined dendrogram order. This
    # step can still be run with no effects, but the runtime may
    # increase. Dendrogram order is stored in
    # `adata.uns["dendrogram_cluster"]["categories_ordered"]`.
    ns.pp.dendrogram(
        adata,
        cluster_header,
        save=True,
        output_folder=output_folder,
        outputfilename_suffix=cluster_header,
    )

    # Calculating cluster medians per gene
    #
    # Note: Run `ns.pp.prep_medians` before running NS-Forest.
    adata = ns.pp.prep_medians(adata, cluster_header)
    # adata

    # Calculating binary scores per gene per cluster
    #
    # Note: Run `ns.pp.prep_binary_scores` before running NS-Forest.
    adata = ns.pp.prep_binary_scores(adata, cluster_header)
    # adata

    # Plotting median and binary score distributions
    # plt.clf()
    # filename = output_folder + cluster_header + "_medians.png"
    # print(f"Saving median distributions as...\n{filename}")
    # a = plt.figure(figsize=(6, 4))
    # a = plt.hist(adata.varm["medians_" + cluster_header].unstack(), bins=100)
    # a = plt.title(
    #     f'{file.split("/")[-1].replace(".h5ad", "")}: {"medians_" + cluster_header} histogram'
    # )
    # a = plt.xlabel("medians_" + cluster_header)
    # a = plt.yscale("log")
    # a = plt.savefig(filename, bbox_inches="tight")
    # plt.show()
    # plt.clf()
    # filename = output_folder + cluster_header + "_binary_scores.png"
    # print(f"Saving binary_score distributions as...\n{filename}")
    # a = plt.figure(figsize=(6, 4))
    # a = plt.hist(adata.varm["binary_scores_" + cluster_header].unstack(), bins=100)
    # a = plt.title(
    #     f'{file.split("/")[-1].replace(".h5ad", "")}: {"binary_scores_" + cluster_header} histogram'
    # )
    # a = plt.xlabel("binary_scores_" + cluster_header)
    # a = plt.yscale("log")
    # a = plt.savefig(filename, bbox_inches="tight")
    # plt.show()

    # Saving preprocessed AnnData as new h5ad
    filename = file.replace(".h5ad", "_preprocessed.h5ad")
    print(f"Saving new anndata object as...\n{filename}")
    adata.write_h5ad(filename)


    # === Running NS-Forest and plotting classification metrics

    # Running NS-Forest
    outputfilename_prefix = cluster_header
    results = nsforesting.NSForest(
        adata,
        cluster_header,
        output_folder=output_folder,
        outputfilename_prefix=outputfilename_prefix,
    )
    # results


    # Plotting classification metrics from NS-Forest results
    # ns.pl.boxplot(results, "f_score")
    # ns.pl.boxplot(results, "PPV")
    # ns.pl.boxplot(results, "recall")
    # ns.pl.boxplot(results, "onTarget")
    # ns.pl.scatter_w_clusterSize(results, "f_score")
    # ns.pl.scatter_w_clusterSize(results, "PPV")
    # ns.pl.scatter_w_clusterSize(results, "recall")
    # ns.pl.scatter_w_clusterSize(results, "onTarget")

    # Plotting scanpy dot plot, violin plot, matrix plot for NS-Forest markers
    #
    # Note: Assign pre-defined dendrogram order here **or** use
    # `adata.uns["dendrogram_" + cluster_header]["categories_ordered"]`.
    # to_plot = results.copy()
    # dendrogram = []  # custom dendrogram order
    # dendrogram = list(adata.uns["dendrogram_" + cluster_header]["categories_ordered"])
    # to_plot["clusterName"] = to_plot["clusterName"].astype("category")
    # to_plot["clusterName"] = to_plot["clusterName"].cat.set_categories(dendrogram)
    # to_plot = to_plot.sort_values("clusterName")
    # to_plot = to_plot.rename(columns={"NSForest_markers": "markers"})
    # to_plot.head()
    # markers_dict = dict(zip(to_plot["clusterName"], to_plot["markers"]))
    # markers_dict
    # ns.pl.dotplot(
    #     adata,
    #     markers_dict,
    #     cluster_header,
    #     dendrogram=dendrogram,
    #     save=True,
    #     output_folder=output_folder,
    #     outputfilename_suffix=outputfilename_prefix,
    # )
    # ns.pl.stackedviolin(
    #     adata,
    #     markers_dict,
    #     cluster_header,
    #     dendrogram=dendrogram,
    #     save=True,
    #     output_folder=output_folder,
    #     outputfilename_suffix=outputfilename_prefix,
    # )
    # ns.pl.matrixplot(
    #     adata,
    #     markers_dict,
    #     cluster_header,
    #     dendrogram=dendrogram,
    #     save=True,
    #     output_folder=output_folder,
    #     outputfilename_suffix=outputfilename_prefix,
    # )
        
def get_datasets(datasets_df):

    datasets_series = [row for index, row in lung_datasets.iterrows()]

    with Pool(8) as p:
        p.map(get_dataset, datasets_series)


if __name__ == "__main__":

    __spec__ = None  # Workaround for Pool() in ipython

    lung_obs, lung_datasets = get_lung_obs_and_datasets()

    titles = get_titles(lung_datasets)

    pmids = get_pmids(titles)

    run_ontogpt(pmids)

    get_datasets(lung_datasets)
