# Reviewed OOXML overrides

This directory contains narrow, user-reviewed replacements for documents that
intentionally diverge from the immutable `base-fde9089` portability baseline.

- `forced-termination-notice-recipient-without-address.docx`: the recipient
  line is `致：用人单位名称`; the employer address remains an input and checklist
  verification field but is no longer displayed on that line. All other builder
  behavior remains covered by the unpacked OOXML comparison.

The eight `*-explicit-fonts.docx` fixtures were regenerated from the immutable
`base-fde9089/inputs.json` after a Windows LibreOffice/Poppler render and visual
review. They materialize `ascii`, `hAnsi`, `eastAsia`, and `cs` fonts on every
run so Tencent Office reserialization cannot make Microsoft Word fall back to
MS Mincho/Cambria. The current reviewed set also uses 1.5-line body spacing,
first-line two-character indentation without left/hanging indentation for
ordered and unordered list paragraphs, simple PAGE/NUMPAGES fields for stable
cross-editor footers, and closing/signature keep-together pagination. Dense
dense arbitration and enforcement applications may compact body text only to
prevent an orphan signature page while keeping signature lines at 1.5 spacing. The matching
`render-expectations.json` records that the enforcement fixture is now one page.
