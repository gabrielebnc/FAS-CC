"""Link this checkout's assignment files into an original UPISAS checkout."""

import argparse
from pathlib import Path
import shutil
import subprocess


FILES = (
    "UPISAS/exemplars/crowdnav.py",
    "UPISAS/strategies/my_strategy.py",
    "run_challenge.py",
)
ROOT = Path(__file__).resolve().parents[1]


def git(directory, *args):
    return subprocess.check_output(["git", "-C", str(directory), *args], text=True).strip()


def install_guards():
    configured = subprocess.run(
        ["git", "-C", str(ROOT), "config", "--get", "core.hooksPath"],
        capture_output=True, text=True,
    )
    if configured.stdout.strip():
        raise SystemExit("Custom core.hooksPath is configured; install the guards there explicitly.")
    hooks = Path(git(ROOT, "rev-parse", "--git-path", "hooks"))
    if not hooks.is_absolute():
        hooks = ROOT / hooks
    hooks.mkdir(parents=True, exist_ok=True)
    for name in ("pre-commit", "pre-push"):
        source, destination = ROOT / ".githooks" / name, hooks / name
        if destination.exists() and destination.read_bytes() != source.read_bytes():
            raise SystemExit(f"Existing hook at {destination}; refusing to overwrite it")
    # Git metadata persists across branches, including the three-file main.
    for name in ("pre-commit", "pre-push"):
        destination = hooks / name
        shutil.copyfile(ROOT / ".githooks" / name, destination)
        destination.chmod(0o755)
    print("Installed main-only commit/push guards in the submission repository.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upisas", type=Path, default=ROOT.parent,
                        help="Original UPISAS checkout (default: this repository's parent)")
    parser.add_argument("--hooks-only", action="store_true",
                        help="Install main guards without linking a backbone checkout")
    args = parser.parse_args()
    if args.hooks_only:
        install_guards()
        return
    upstream = args.upisas.resolve()
    if upstream == ROOT or not (upstream / "UPISAS/strategy.py").is_file():
        parser.error("--upisas must point to a separate original UPISAS checkout")
    if Path(git(upstream, "rev-parse", "--show-toplevel")).resolve() != upstream:
        parser.error("--upisas must be the root of the original UPISAS Git repository")

    # Validate the complete operation before changing any assignment files.
    for name in FILES:
        source, destination = ROOT / name, upstream / name
        if not source.is_file():
            parser.error(f"Missing {source}; check out development before linking")
        if destination.is_symlink() and destination.resolve() == source:
            continue
        backup = upstream / ".fas-cc-backup" / name
        if destination.exists() or destination.is_symlink():
            if backup.exists() or backup.is_symlink():
                parser.error(f"Backup already exists: {backup}; resolve it before replacing {destination}")
    install_guards()
    for name in FILES:
        source, destination = ROOT / name, upstream / name
        if destination.is_symlink() and destination.resolve() == source:
            continue
        backup = upstream / ".fas-cc-backup" / name
        if destination.exists() or destination.is_symlink():
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(destination), str(backup))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(source)
        print(f"Linked {destination} -> {source}")

    exclude = Path(git(upstream, "rev-parse", "--git-path", "info/exclude"))
    if not exclude.is_absolute():
        exclude = upstream / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)
    patterns = ["/.fas-cc-backup/", *("/" + name for name in FILES)]
    if ROOT.is_relative_to(upstream):
        patterns.append("/" + ROOT.relative_to(upstream).as_posix() + "/")
    content = exclude.read_text() if exclude.exists() else ""
    for pattern in patterns:
        if pattern not in content.splitlines():
            content += "\n" + pattern + "\n"
    exclude.write_text(content)

    print(f"Edit and commit in {ROOT}; run the linked entry point from {upstream}.")


if __name__ == "__main__":
    main()
