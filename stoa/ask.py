"""Answer one question about one drawing.

    uv run python -m stoa.ask <drawing> "<question>"
"""

import json
import sys

from stoa.pipeline import answer


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    res = answer(sys.argv[1], sys.argv[2])
    out = res.to_json()
    if out["answer"]:
        out["answer"]["evidence"] = out["answer"]["evidence"][:10]
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
