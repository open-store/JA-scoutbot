"""Print the contract spec as JSON: ``python -m scout.contract > docs/contracts/cx-voc-contract.v1.json``."""

import json

from scout.contract.spec import spec_document


def render_spec() -> str:
    return json.dumps(spec_document(), indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    print(render_spec(), end="")
