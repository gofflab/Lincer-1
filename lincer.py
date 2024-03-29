#!/usr/bin/env python

import sys
try:
    sample_sheet = sys.argv[1]
    ref_gtf = sys.argv[2]
    lnc_gtf = sys.argv[3]
except IndexError:
    print '''Usage:
    %s SAMPLE_SHEET REFERENCE_GTF LNCRNA_GTF

    SAMPLE_SHEET is a two-column tab delimited table with no header, which maps
    sample names to paths of GTFs containing de novo transcript assemblies from,
    eg, Cufflinks.

    Column  Column
    Number  Name         Example           Description
    ------  -----------  ----------------  -----------------------------------
    1       sample_name  WT_day0_rep1      the condition label for this sample
    2       gtf_path     WT_day0_rep1.gtf  path to gtf of de novo transcripts

    REFERENCE_GTF contains all annotated transcripts.
    LNCRNA_GTF contains all known lncRNA transcripts.
    ''' % sys.argv[0].split('/')[-1]
    sys.exit(1)

import pandas as pd
import subprocess
import shutil
import os
import csv
from glob import glob

def load_sample_sheet(sample_sheet):
    '''
    Load the sample sheet.

    Expected format (no header):
    [sample_name]\t[path_to_gtf]

    Returns a DataFrame with:
    * sample name (index)
    * path to input gtf
    * path to output gtf
    '''
    print >> sys.stderr, 'Loading sample sheet.'
    print >> sys.stderr, '  src: %s' % sample_sheet

    samples = pd.read_table(sample_sheet, header=None, index_col=0, names=['gtf_path'])
    samples.index.name = 'sample'
    samples['gtf_out'] = samples.index + '.novel.gtf'

    return samples

def find_novel_transcripts(sample, gtf_in, ref_gtf, gtf_out):
    '''
    Find all novel, long, well-covered, multi-exonic transcripts
    from de novo transcript assemblies generated by Cufflinks.
    '''
    print >> sys.stderr, 'Processing:', sample
    print >> sys.stderr, '  Summary: %s.summary.tsv' % sample
    print >> sys.stderr, '  GTF Out: %s.novel.gtf' % sample

    # Load relevant GTF columns.
    x = pd.read_table(gtf_in, header=None, usecols=[2, 3, 4, 8])
    x.columns = ['feature', 'start', 'end', 'attrs']
    x = x[x.feature == 'exon']

    # Extract relevant features from attrs column.
    x['gene_id'] = x.attrs.str.split('gene_id').str[1].str.split('"').str[1]
    x['transcript_id'] = x.attrs.str.split('transcript_id').str[1].str.split('"').str[1]
    x['length'] = x.end - x.start + 1
    x['exons'] = 1
    x['coverage'] = x.attrs.str.split('cov').str[1].str.split('"').str[1].astype(float)

    x = x[['gene_id', 'transcript_id', 'length', 'exons', 'coverage']]

    t_length = x.groupby('transcript_id')[['length']].sum()
    t_num_exons = x.groupby('transcript_id')[['exons']].sum()
    t_coverage = x.groupby('transcript_id')[['coverage']].max()

    # Compare transcripts with reference gtf.
    t_class_code = _get_cuffcompare_class_codes(ref_gtf, gtf_in)

    # Generate table summarizing all filter criteria.
    y = t_length.join(t_num_exons).join(t_coverage).join(t_class_code)
    y.to_csv(sample + '.summary.tsv', sep='\t')

    # Apply filters to table.
    z = y[
        (y.length >= 200)
        & (y.exons > 1)
        & (y.coverage >= 3.0)
        & (y.class_code.isin(['u', 'j', 'i', 'x']))
    ]

    # Generate a filtered GTF.
    _filter_gtf_by_transcript(gtf_in, gtf_out, z.index)

def _get_cuffcompare_class_codes(ref_gtf, gtf):

    symlink_created = False

    # If the input GTF is not in the current directory,
    # create a symlink to it, or cufflinks will put its
    # output somewhere inconvenient.
    gtf_local = gtf.split('/')[-1]
    if not os.path.exists(gtf_local):
        os.symlink(gtf, gtf_local)
        symlink_created = True

    # Run cuffcompare against reference gtf.
    cmd = 'cuffcompare -r %s %s' % (ref_gtf, gtf_local)
    subprocess.check_call(cmd,
                          shell=True,
                          stdout=open('cuffcmp.stdout', 'w'),
                          stderr=open('cuffcmp.stderr', 'w'))

    # Extract information from cuffcompare tmap file.
    class_codes = pd.read_table('cuffcmp.%s.tmap' % gtf_local)
    class_codes = class_codes.set_index('cuff_id')
    class_codes.index.name = 'transcript_id'
    class_codes = class_codes[['class_code', 'ref_id', 'ref_gene_id']]
    class_codes = class_codes.sort_index()

    # Remove cuffcompare output files.
    for junk_file in glob('cuffcmp.*'):
        os.remove(junk_file)

    # Remove symlink if one was created.
    if symlink_created:
        os.remove(gtf_local)

    return class_codes

def _filter_gtf_by_transcript(gtf_in, gtf_out, transcripts_to_keep):

    outfid = open(gtf_out, 'w')
    infid = open(gtf_in, 'r')

    for line in infid:

        # Skip empty lines and comments.
        if len(line) == 0 or line[0] == '#':
            continue

        # Extract transcript_id.
        transcript_id = line.split('transcript_id')[1].split('"')[1]

        # Write selected lines to output GTF.
        if transcript_id in transcripts_to_keep:
            outfid.write(line)

    infid.close()
    outfid.close()

def merge_novel_transcripts(sample_info):
    # Write manifest for cuffmerge.
    sample_info[['gtf_out']].to_csv('novel_transcript_gtfs.txt', header=None, index=None)

    # Run cuffmerge.
    subprocess.check_call('cuffmerge novel_transcript_gtfs.txt',
                          shell=True,
                          stdout=open('cuffmerge.stdout', 'w'),
                          stderr=open('cuffmerge.stderr', 'w'))

    # Retrieve merged GTF.
    shutil.move('merged_asm/merged.gtf', 'novel_transcripts.gtf')

    # Remove cuffmerge files.
    shutil.rmtree('merged_asm')
    os.remove('cuffmerge.stdout')
    os.remove('cuffmerge.stderr')
    os.remove('novel_transcript_gtfs.txt')

def classify_novel_transcripts(ref_gtf, lnc_gtf):

    print >> sys.stderr, 'Classifying novel transcripts.'
    print >> sys.stderr, '  ref gtf:', ref_gtf
    print >> sys.stderr, '  lnc gtf:', lnc_gtf

    #
    # 1. Compare to ref_gtf and lnc_gtf; extract cufflinks class_codes.
    #
    novel_vs_ref = _get_cuffcompare_class_codes(ref_gtf, 'novel_transcripts.gtf')
    novel_vs_lnc = _get_cuffcompare_class_codes(lnc_gtf, 'novel_transcripts.gtf')
    x = novel_vs_ref.join(novel_vs_lnc, lsuffix='__all', rsuffix='__lnc')

    #
    # 2. Classify novel transcripts based on cufflinks class_codes.
    #
    x['classification'] = '.'
    x.loc[(x.classification == '.') & (x.class_code__lnc == '='), 'classification'] = 'known_isoform'
    x.loc[(x.classification == '.') & (x.class_code__lnc == 'j')
                                    & (x.class_code__all == 'j')
                                    & (x.ref_gene_id__lnc != x.ref_gene_id__all), 'classification'] = 'possible_artifact'
    x.loc[(x.classification == '.') & (x.class_code__lnc == 'j'), 'classification'] = 'novel_isoform'
    x.loc[(x.classification == '.') & (x.class_code__all == 'u'), 'classification'] = 'intergenic'
    x.loc[(x.classification == '.') & (x.class_code__all == 'x')
                                    & (x.class_code__lnc == 'u'), 'classification'] = 'antisense'
    x.loc[(x.classification == '.') & (x.class_code__all == 'i'), 'classification'] = 'intronic'
    x.loc[(x.classification == '.'), 'classification'] = 'not_a_lncRNA'

    x.to_csv('novel_transcripts.tsv', sep='\t')

def fold_novel_lncs_into_input_gtfs(lnc_gtf, out_gtf):

    print >> sys.stderr, 'Writing lncRNA GTF.'
    print >> sys.stderr, '  final gtf:', out_gtf

    #
    # 1. Process known lncRNAs.
    #

    # Load lnc_gtf; extract relevant info from attr column.
    known_trans_gtf = pd.read_table(lnc_gtf, header=None, comment='#')
    known_trans_gtf = known_trans_gtf[known_trans_gtf[2] == 'exon']

    known_trans_gtf['gene_id'] = known_trans_gtf[8].str.split('gene_id').str[1].str.split('"').str[1]
    known_trans_gtf['transcript_id'] = known_trans_gtf[8].str.split('transcript_id').str[1].str.split('"').str[1]
    known_trans_gtf['gene_name'] = known_trans_gtf[8].str.split('gene_name').str[1].str.split('"').str[1]
    known_trans_gtf = known_trans_gtf.drop(8, axis=1)

    #
    # 2. Load novel lncRNA info.
    #

    # Load novel transcripts gtf; extract relevant info from attr column.
    novel_trans_gtf = pd.read_table('novel_transcripts.gtf', header=None)
    novel_trans_gtf['gene_id'] = novel_trans_gtf[8].str.split('gene_id').str[1].str.split('"').str[1]
    novel_trans_gtf['transcript_id'] = novel_trans_gtf[8].str.split('transcript_id').str[1].str.split('"').str[1]
    novel_trans_gtf = novel_trans_gtf.drop(8, axis=1)

    # Load novel transcript classifications.
    novel_trans_annots = pd.read_table('novel_transcripts.tsv', index_col=0)

    #
    # 3. Process novel isoforms of known lncRNAs.
    #

    # Isolate novel isoforms of known genes.
    novel_isoform_annots = novel_trans_annots[novel_trans_annots.classification == 'novel_isoform']
    novel_isoform_gtf = novel_trans_gtf[novel_trans_gtf.transcript_id.isin(novel_isoform_annots.index)]

    # Replace XLOCs with official gene names from reference GTF.
    trans_id_to_gene_name = novel_trans_annots.ref_gene_id__lnc
    gene_name_to_gene_id = known_trans_gtf[['gene_name', 'gene_id']].groupby('gene_name').first().gene_id
    novel_isoform_gtf['gene_name'] = novel_isoform_gtf.transcript_id.map(trans_id_to_gene_name)
    novel_isoform_gtf.loc[:, 'gene_id'] = novel_isoform_gtf.gene_name.map(gene_name_to_gene_id)

    #
    # 4. Process completely novel lncRNAs.
    #

    # Isolate completely novel genes.
    novel_gene_annots = novel_trans_annots[novel_trans_annots.classification.isin([
        'intergenic',
        'antisense',
        'intronic',
    ])]
    novel_gene_gtf = novel_trans_gtf[novel_trans_gtf.transcript_id.isin(novel_gene_annots.index)]

    # Use XLOCs as gene names.
    novel_gene_gtf['gene_name'] = novel_gene_gtf.gene_id

    #
    # 5. Merge these three tables.
    #
    df = pd.concat([known_trans_gtf, novel_isoform_gtf, novel_gene_gtf])

    #
    # 6. Sort, grouping isoforms by gene.
    #
    start_pos = df.groupby('gene_id')[[3]].min().rename(columns={3: 'start_pos'})
    df = pd.merge(df, start_pos, how='left', left_on='gene_id', right_index=True)
    df = df.sort([0, 'start_pos', 'gene_id', 'transcript_id'])
    df = df.reset_index(drop=True)

    #
    # 7. Convert table to gtf; write to disk.
    #
    df[8] = 'gene_id "' + df.gene_id + '"; transcript_id "' + df.transcript_id + '"; gene_name "' + df.gene_name + '";'
    df = df.loc[:, range(9)]
    df.to_csv(out_gtf, sep='\t', header=None, index=None, quoting=csv.QUOTE_NONE)


#  __  __      _
# |  \/  |__ _(_)_ _
# | |\/| / _` | | ' \
# |_|  |_\__,_|_|_||_|
#
sample_info = load_sample_sheet(sample_sheet)

for sample, gtf_in, gtf_out in sample_info.itertuples():
    find_novel_transcripts(sample, gtf_in, ref_gtf, gtf_out)

merge_novel_transcripts(sample_info)
classify_novel_transcripts(ref_gtf, lnc_gtf)
fold_novel_lncs_into_input_gtfs(lnc_gtf, 'lncRNA_catalog.gtf')
