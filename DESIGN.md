# Design

<!-- impeccable:design-schema 1 -->

## Direction contract

**THESIS:** A professional compliance dashboard where power users rapidly scan data-dense displays to identify, review, and remediate findings - accuracy and speed take precedence over visual playfulness.

**OWN-WORLD:** Clean white background (`#FFFFFF` → `#F8FAFC`) with professional navy blue text (`#0F172A`), navy blue primary accent (`#1E3A8A`) for navigation and actions, sky blue (`#0EA5E9`) for interactive states. Inter font family for maximum legibility. Modern header with 2px borders, subtle shadows (0 1px 3px rgba(0,0,0,0.05)), and consistent 24px spacing. Data tables with soft gray backgrounds (#F8FAFC), semantic color badges with proper contrast, and clear visual hierarchy through navy blue headings.

**STORY:** Users land on a status overview where the overall health of compliance programs is immediately clear. From there, they drill into findings queues, filter and triage by severity/owner/rule, then review evidence in focused detail with one-click disposition. Every screen prioritizes scanability over decoration.

**FIRST VIEWPORT:** Top bar with global status and period selector. Below, three-column layout: (1) left sidebar with navigation and quick filters, (2) central column with status tiles (open findings, exposure, coverage, last run), (3) right column with domain health and recent runs. Data tables use compact row heights (40px), with severity badges in the left margin and exposure in currency column.

**FORM:** Financial data platform, position 2 on the familiarity spectrum - enterprise software with proven patterns for professional audiences.

**FINISH:** Unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance.
