# Release download layout

Each new complete release uploads exactly three assets:

| Asset | Purpose |
| --- | --- |
| arbibuddy-workbuddy.zip | WorkBuddy installation with locked document dependencies and portable identity |
| arbibuddy-<VERSION>-source-materials.zip | Fixed public project source, corresponding dependency sources, complete notices/licenses, modifications, build/replacement instructions, manifests, gate and sanitized evidence |
| SHA256SUMS.txt | SHA-256 of the two ZIP files above only |

The materials ZIP has its own complete member inventory. Keep original dependency archives and provenance; include pure Python sources as well as required native sources. GitHub automatic source archives do not replace this package. Inner manifests do not contain the hash of their own containing archive; an outer delivery manifest binds final archive hashes.

Release notes put the WorkBuddy ZIP download and `npx skills add windstudio/arbibuddy` first. Developer details may be folded below. The version tag targets the clean public `codex/source` commit, not generated main. Verify all three uploaded assets by unauthenticated download.

SkillHub ZIP, upload manifest and changelog are local manual-upload deliverables, not a fourth Release attachment. Historical versions keep their original layout. See [release workflow](release-workflow.md) and [skills distribution](skills-distribution.md).
