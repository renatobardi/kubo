---
name: kubo-design
description: Use this skill to generate well-branded interfaces and assets for Kubo, either for production or throwaway prototypes/mocks/etc. Contains essential design guidelines, colors, type, fonts, assets, and UI kit components for prototyping.
user-invocable: true
---

Read the README.md file within this skill, and explore the other available files.
If creating visual artifacts (slides, mocks, throwaway prototypes, etc), copy assets out and create static HTML files for the user to view. If working on production code, you can copy assets and read the rules here to become an expert in designing with this brand.
If the user invokes this skill without any other guidance, ask them what they want to build or design, ask some questions, and act as an expert designer who outputs HTML artifacts _or_ production code, depending on the need.

## Quick reference
- **Fonts**: Noto Serif (ALL headings/titles), Inter (all body/UI). App density is 14px (`text-sm`).
- **Color**: warm stone neutrals + **near-black mono primary** (ChatGPT-style; dark inverts to near-white). Never a saturated accent for chrome. Tokens in `tokens/colors.css` (OKLCH, light + `.dark`).
- **Shape**: pill buttons/inputs (`--radius-4xl`), cards `--radius-2xl` defined by `ring-1 ring-foreground/10` (no shadow).
- **Signature rules**: destructive is tinted (10% bg + red text), charts are monochrome stone, agent identity is a preset emoji.
- **Icons**: Lucide via the `Icon` component. **Emoji only as agent avatars** (`assets/agent-emojis.js`).
- **Components**: `components/*/` — link `styles.css`, load `_ds_bundle.js`, read `const { Button } = window.KoboDesignSystem_6efae6`.
- **Full app recreation**: `ui_kits/kubo-app/`.
