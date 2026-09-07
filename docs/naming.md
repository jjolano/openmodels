# Model names and evidence

Every model appears in the main directory, whether or not a published nickname is known.
Historical status describes absence from the current upstream tree; it is independent of
naming. The SDK and API include historical models by default. Consumers still apply their
own support policies and select exact recipe IDs.

## Name selection

1. Reviewed published names take precedence: comma, the community wiki, Sunnypilot,
   then other attributed fork sources. Other naming claims remain available in `names`.
2. A narrowly recognized introducing commit title can supply a **source-derived label**.
   This is explicitly labelled as an extraction, not a verified community nickname.
3. Otherwise generate a label from type, architecture, variant, source date and a short
   bundle ID. UUIDs, build commands, generic update titles and revert titles are not nicknames.

`index/model_names.json` stores reviewed records with an exact archived occurrence commit
and complete role/SHA-256 artifact set. Import rejects records that no longer match their
bundle. Standard/big models and different role sets remain separate. Display changes do not
change model IDs, recipe bytes or any host settings. A shared encoder, date, nickname or
checkpoint alone never establishes the identity of a complete model.

The site and SDK expose `name_kind` (`published`, `source`, `generated`) and `names`, an
array of attributed claims containing `name`, `source`, `url` and `method`. The primary
published name follows source precedence; aliases remain searchable and visible on detail
pages. Original source titles and exact configurations remain available underneath.

## Reviewed sources

- **Comma PRs:** confirm the PR merged, retrieve its original merge commit, enumerate
  the full model tree, and compare each relevant ONNX LFS pointer's filename, SHA-256 and
  size with the archived bundle. The merge commit must occur in that bundle's history.
  Pinned commit pages in each record preserve the introducing title. Intermediate PR
  revisions and unchanged companion models do not inherit a name automatically.
- **Community wiki:** [pinned Driving Models page](https://github.com/commaai/openpilot/wiki/Driving-Models/cccb02396dd1afe16ccc8eb172c33efc3f06f4a4).
  Only explicit name-to-PR links traced through the same exact commit and artifact checks
  are imported. Date-only entries remain unresolved. This is community attribution, not
  a comma endorsement.
- **Sunnypilot:** [pinned catalog](https://raw.githubusercontent.com/sunnypilot/sunnypilot-models/947018e4697e2f68c898438c5aa7597bab651335/docs/driving_models_v18.json).
  Names associate an exact upstream commit and artifact bundle with a listing. Compiled
  packages and tuning overrides are separate from the original ONNX configurations.

The checked [FrogPilot v17 catalog](https://raw.githubusercontent.com/FrogAi/FrogPilot-Resources/09f1cccd16e3d463e84776c7e2a8aaa690b4c78c/model_names_v17.json)
contains names, IDs and runtime versions, but no source commits or artifact hashes. Its
36 choices were examined; none were attached by name similarity alone. A source/build
manifest or equivalent exact provenance is needed to add those associations.

Naming references are reviewed, pinned data. Site builds require no live naming-source
requests. Future archive entries receive deterministic source or generated labels immediately;
additional published names enter through reviewed updates to the naming records.
