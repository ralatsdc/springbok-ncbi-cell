#!/usr/bin/env nextflow

def helpMessage() {
    log.info """
    Usage: nextflow run nsforest-single-process.nf --h5adPath '../data/cellxgene-sample/*.H5AD'

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
csvFilename = channel.value("cell_type_results.csv")

process run_nsforest {

    publishDir "results", mode: "copy"

    input:
    path h5adPath
    val csvFilename

    output:
    path "*.csv"

    script:
    baseName = h5adPath.getBaseName()
    """
    nsforest.py --run-nsforest-with-preprocessing ${h5adPath}
    mv ${csvFilename} ${baseName}_${csvFilename}
    """
}

workflow {
    run_nsforest(h5adPath, csvFilename)
}
