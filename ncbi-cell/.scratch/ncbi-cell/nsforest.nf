#!/usr/bin/env nextflow

def helpMessage() {
    log.info """
    Usage: nextflow run nsforest.nf --cxgPaths 'cxgPath/*.HDA5' --csvBasePath "csvBasePath"
           nextflow run nsforest.nf --cxgPaths '../../data/cellxgene-sample/*.H5AD' --csvBasePath "$PWD/results"

    Options:
    --help
        Show help message
    """.stripIndent()
}

params.cxgPaths = ""
params.csvBasePath = ""
params.help = ""

// Show help message, if requested
if (params.help) {
  helpMessage()
  exit 0
}

cxgPaths = channel.fromPath(params.cxgPaths)
csvBasePath = channel.value(params.csvBasePath)
csvFilename = channel.value("cell_type_results.csv")

process nsforest {

    container "ralatsdio/nsforst:latest"
    conda "/opt/conda/envs/nsforest"

    input:
    path hda5Filepath
    val csvBasePath
    val csvFilename

    output:
    path "$csvFilename"

    script:
    baseName = hda5Filepath.getBaseName()
    """
    mkdir -p ${csvBasePath}/${baseName}
    nsforest.py ${hda5Filepath}
    cp ${csvFilename} ${csvBasePath}/${baseName}
    mv pp_${hda5Filepath} ${csvBasePath}/${baseName}
    """
}

workflow {
    nsforest(cxgPaths, csvBasePath, csvFilename)
}
