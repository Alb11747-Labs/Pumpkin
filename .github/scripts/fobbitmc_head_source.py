"""Compare build inputs with checkout bytes derived only from committed attributes."""

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile


def require(predicate, message):
    if not predicate:
        raise ValueError(message)


def sha(path):
    require(path.is_file() and not path.is_symlink(), "Not a regular source file: " + str(path))
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_provenance(root, commit):
    root = Path(root).resolve(strict=True)
    clean_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    clean_env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                     GIT_CONFIG_SYSTEM=os.devnull, GIT_ATTR_NOSYSTEM="1",
                     GIT_NO_LAZY_FETCH="1", GIT_NO_REPLACE_OBJECTS="1")

    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], env=clean_env)

    require(git("rev-parse", "HEAD").decode().strip() == commit, "Source HEAD mismatch: " + root.name)
    entries = {}
    for row in git("ls-tree", "-r", "-z", commit).split(b"\0"):
        if not row:
            continue
        metadata, encoded_name = row.split(b"\t", 1)
        mode, kind, oid = metadata.decode("ascii").split()
        name = encoded_name.decode("utf-8")
        require(mode in {"100644", "100755"} and kind == "blob", "Unsupported source mode: " + name)
        require(name not in entries, "Duplicate source path: " + name)
        entries[name] = (mode, oid)
    indexed = {}
    for row in git("ls-files", "--stage", "-z").split(b"\0"):
        if row:
            metadata, name = row.split(b"\t", 1)
            mode, oid, stage = metadata.decode("ascii").split()
            name = name.decode("utf-8")
            require(stage == "0" and name not in indexed, "Unmerged or duplicate index path")
            indexed[name] = (mode, oid)
    require(indexed == entries, "Index differs from committed source")
    require(not git("diff", "--no-ext-diff", "--no-textconv", "HEAD", "--"), "Dirty tracked source")
    require(not git("ls-files", "--others", "--exclude-standard", "-z"), "Untracked build input")
    for name in (".cargo/config", ".cargo/config.toml"):
        require(not (root / name).exists() or name in entries, "Untracked Cargo configuration")

    objects = Path(os.fsdecode(git("rev-parse", "--git-path", "objects")).strip())
    if not objects.is_absolute():
        objects = root / objects
    require(objects.is_dir() and not objects.is_symlink(), "Missing physical source object store")
    # Adapted from the independently reviewed R3 local qualifier. A private index,
    # object store binding and HEAD attribute source prevent info/global/worktree
    # attributes from redefining expected bytes; no custom filter can execute.
    with tempfile.TemporaryDirectory(prefix="fobbitmc-head-source-") as temporary:
        temporary = Path(temporary)
        repository, worktree, template = (temporary / name for name in ("git", "worktree", "template"))
        worktree.mkdir()
        template.mkdir()
        subprocess.check_output(["git", "init", "--bare", "--quiet", "--template=" + str(template), str(repository)], env=clean_env)
        env = {**clean_env, "GIT_OBJECT_DIRECTORY": str(objects.resolve(strict=True)),
               "GIT_INDEX_FILE": str(temporary / "index"), "GIT_ATTR_SOURCE": commit}
        argv = ["git", "--git-dir=" + str(repository), "--work-tree=" + str(worktree),
                "-c", "core.bare=false", "-c", "core.eol=lf", "-c", "core.fsmonitor=false",
                "-c", "core.attributesFile=" + os.devnull]

        def isolated(*args, input_data=None):
            return subprocess.check_output([*argv, *args], input=input_data, env=env)

        isolated("read-tree", commit)
        attributes = isolated("check-attr", "--source=" + commit, "-z", "--all", "--stdin",
                              input_data=b"".join(name.encode("utf-8") + b"\0" for name in entries))
        fields = attributes.split(b"\0")
        require(fields[-1] == b"" and (len(fields) - 1) % 3 == 0, "Malformed committed attributes")
        allowed = {"text": {"set", "unset", "auto"}, "eol": {"lf", "crlf"},
                   "binary": {"set", "unset"}, "diff": {"unset"}, "merge": {"unset"}}
        seen = set()
        for offset in range(0, len(fields) - 1, 3):
            name, attribute, value = (field.decode("utf-8") for field in fields[offset:offset + 3])
            require(name in entries and (name, attribute) not in seen, "Unexpected committed attribute path")
            seen.add((name, attribute))
            require(attribute in allowed and value in allowed[attribute],
                    "Unsupported committed checkout filter/driver: " + name + " " + attribute + "=" + value)
        isolated("checkout-index", "--all")
        expected = {name: sha(worktree / name) for name in entries}
        actual = {name: sha(root / name) for name in entries}
        mismatches = [name for name in entries if actual[name] != expected[name]]
        require(not mismatches, "Tracked bytes differ from HEAD-derived checkout: " + ", ".join(mismatches))
    return {"commit": commit, "tree": git("rev-parse", "HEAD^{tree}").decode().strip(), "files": actual}
