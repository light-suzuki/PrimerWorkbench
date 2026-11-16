# Primer table import

Primer Reverse Lookup reads pasted tables, FASTA, `.xlsx`, CSV and TSV locally
in the browser. No workbook is uploaded to the API. Legacy `.xls` files must be
saved as `.xlsx` or copied as cells. Workbook formulas are not executed; cached
values are read. Select the worksheet explicitly.

Automatic pairing prioritizes Fw/Re (including fw/rv) headers and primer-name
suffixes. Without direction information, consecutive rows within each column
are paired independently. Parallel vertical tables are supported. Select the
horizontal layout for headerless two-primer rows.

Position, size, quality and plate-well columns can accompany the sequences.
Directional primer names take priority over preceding plate-well identifiers.
Numbered direction suffixes keep variants separate. Full-width characters,
spaces, IUPAC bases and 5-prime/3-prime notation are normalized. Written
3-prime-to-5-prime sequences are reversed, with a warning, not complemented.

Review names, source cells and sequences before applying. Exclude pairs or swap
their direction when needed. Unpaired entries are displayed and excluded from
search. Automatic parsing cannot establish biological pairing when metadata is
missing. Files are limited to 20 MB, decoded XML to 32 MB, and each import to
500 pairs. Tests use synthetic sequences only.

## Order sheets

Expand the order-sheet section after reviewing the selected pairs. Edit oligo
names and enter synthesis scale, purification, delivery and notes as needed.
Unspecified settings remain blank. Download either a generic Excel order sheet
or a two-column Name/Sequence workbook for copying into a vendor portal.
Identical sequences are retained with a warning; duplicate names block export.
Sequences are kept in their reviewed 5-prime-to-3-prime order.

Official Eurofins standard DNA order forms (Japanese and English) are linked
alongside the current official form list and FASMAC/IDT ordering guides. Vendor
files are not redistributed. The two-column export is
not represented as a vendor-specific upload template. Set vendor options and
contact/shipping information at the vendor. No purchase or order is submitted.
This export covers unmodified DNA; use vendor-specific forms for modifications.

## Manufacturer formats

The manufacturer selector includes Eurofins, NIPPON GENE, Thermo Fisher /
Invitrogen, IDT, FASMAC, HSS and Merck / Sigma-Aldrich. Entries distinguish
direct public templates from official Web ordering guides; not every vendor has
a publicly downloadable form or an automatic adapter.

- Invitrogen Value Oligos: dedicated XLSX uses the verified official `Oligos`
  worksheet and Name/Sequence column headers. Blocks more than 200 oligos,
  sequences outside 5–40 nt and bases other than ACGT. The vendor configures
  synthesis options and may restrict product availability by region.
- NIPPON GENE: download the official standard (0.05 micromol) or small-scale
  (0.02 micromol) blank XLSX, then select it locally. The app fills only G/H cells
  starting at row 7, preserving styles, formulas and other workbook entries.
  It accepts up to 96 oligos, requires names of at most 15 ASCII alphanumeric
  characters and rejects already populated or structurally different forms.
  Generic export settings are not copied. Vendor defaults remain in the form;
  open the draft in Excel, recalculate, review synthesis options, complete
  customer details and save before submitting. No vendor import acceptance
  or Excel recalculation is claimed by file generation alone.

Sources checked against official sites and actual public workbooks on
2026-09-27: [NIPPON GENE forms](https://nippongene-oligo.com/support/downloads/),
[Invitrogen Value Oligos](https://www.thermofisher.com/order/custom-oligo/enterSequences),
[FASMAC ordering](https://fasmac.co.jp/dna_rna_order_flow-2),
[HSS ordering](https://hssnet.co.jp/order/flow/).
