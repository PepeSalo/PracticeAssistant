from __future__ import annotations

import argparse

from .commands import normalize


def word_error_rate(intended: str, heard: str) -> float:
    reference, actual = normalize(intended).split(), normalize(heard).split()
    if not reference:
        raise ValueError("Intended transcript must contain words.")
    previous = list(range(len(actual) + 1))
    for i, word in enumerate(reference, 1):
        current = [i]
        for j, other in enumerate(actual, 1):
            current.append(
                min(previous[j] + 1, current[-1] + 1, previous[j - 1] + (word != other))
            )
        previous = current
    return previous[-1] / len(reference)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare intended and heard speech.")
    parser.add_argument("--intended", required=True)
    parser.add_argument("--heard", required=True)
    args = parser.parse_args(argv)
    try:
        rate = word_error_rate(args.intended, args.heard)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Word error rate: {rate:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
