#!/usr/bin/env nextflow

def helpMessage() {
    log.info """
    Usage: nextflow run nsforest-multiple-processes.nf --h5adPath '../data/cellxgene-sample/*.H5AD'

    Options:
    --help
        Show help message
    """.stripIndent()
}

params.h5adPath = ""
params.help = ""

// Show help message, if requested
if (params.help) {
  helpMessage()
  exit 0
}

h5adPath = channel.fromPath(params.h5adPath)

clusterHeader = channel.value("cell_type")
totalCounts = channel.value(5000000)
csvFilename = channel.value("cell_type_results.csv")

process downsample_adata_file {

    publishDir "results", mode: "copy"

    input:
    path h5adPath
    val totalCounts

    output:
    path "*_ds.*", emit: dsH5adPath

    script:
    """
    nsforest.py --downsample-adata-file ${h5adPath} --total-counts ${totalCounts}
    """

}

process generate_scanpy_dendrogram {

    publishDir "results", mode: "copy"

    input:
    path h5adPath
    val clusterHeader

    output:
    path "*_gd.*", emit: gdH5adPath

    script:
    """
    nsforest.py --generate-scanpy-dendrogram ${h5adPath} --cluster-header ${clusterHeader}
    """

}

process calculate_cluster_medians_per_gene {

    publishDir "results", mode: "copy"

    input:
    path h5adPath
    val clusterHeader

    output:
    path "*_cc.*", emit: ccH5adPath

    script:
    """
    nsforest.py --calculate-cluster-medians-per-gene ${h5adPath} --cluster-header ${clusterHeader}
    """

}

process calculate_binary_scores_per_gene_per_cluster {

    publishDir "results", mode: "copy"

    input:
    path h5adPath
    val clusterHeader

    output:
    path "*_cb.*", emit: cbH5adPath

    script:
    """
    nsforest.py --calculate-binary-scores-per-gene-per-cluster ${h5adPath} --cluster-header ${clusterHeader}
    """

}

process run_nsforest {

    publishDir "results", mode: "copy"

    input:
    path h5adPath
    val clusterHeader
    val csvFilename

    output:
    path "*.csv"

    script:
    baseName = h5adPath.getBaseName()
    """
    nsforest.py --run-nsforest-without-preprocessing ${h5adPath} --cluster-header ${clusterHeader}
    mv ${csvFilename} ${baseName}_${csvFilename}
    """

}

workflow {

    dsH5adPath = downsample_adata_file(h5adPath, totalCounts)

    gdH5adPath = generate_scanpy_dendrogram(dsH5adPath, clusterHeader)

    ccH5adPath = calculate_cluster_medians_per_gene(gdH5adPath, clusterHeader)

    cbH5adPath = calculate_binary_scores_per_gene_per_cluster(ccH5adPath, clusterHeader)

    run_nsforest(cbH5adPath, clusterHeader, csvFilename)

}
