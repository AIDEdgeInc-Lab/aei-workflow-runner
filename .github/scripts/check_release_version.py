"""Release gate: the release tag, the project version and the files that will be uploaded must all agree.

Run by the publish workflow after `python -m build`, before anything is uploaded. Any mismatch exits non-zero, which stops the job and
therefore the upload. Standard library only.

Environment (set by the workflow): EVENT (github.event_name), RELEASE_TAG (release tag name, release events), TARGET (workflow_dispatch
input), REF_TYPE / REF_NAME (github.ref_type / github.ref_name), GITHUB_SHA (the commit being released).
"""
import glob
import os
import re
import sys
import tarfile
import zipfile
from email.parser import Parser


def fail(message):
    print(f"::error::release gate: {message}")
    sys.exit(1)


def main(dist="dist"):
    event = os.environ.get("EVENT", "")
    sha = os.environ.get("GITHUB_SHA", "(not set)")
    wheels = sorted(glob.glob(os.path.join(dist, "*.whl")))
    sdists = sorted(glob.glob(os.path.join(dist, "*.tar.gz")))
    if len(wheels) != 1 or len(sdists) != 1:
        fail(f"expected exactly one wheel and one sdist in {dist}/, found {len(wheels)} and {len(sdists)}")

    with zipfile.ZipFile(wheels[0]) as z:
        meta_name = next((n for n in z.namelist() if n.endswith(".dist-info/METADATA")), None)
        if meta_name is None:
            fail("the wheel has no METADATA")
        meta = Parser().parsestr(z.read(meta_name).decode("utf-8"))
        name, version = meta["Name"], meta["Version"]
        # A literal __version__ / VERSION in the package source must say the same thing as the metadata.
        declared = set()
        for member in z.namelist():
            if member.endswith(".py") and ".dist-info/" not in member and member.count("/") == 1:
                text = z.read(member).decode("utf-8", "replace")
                declared.update(re.findall(r'^(?:__version__|VERSION)\s*=\s*["\']([^"\']+)["\']', text, re.M))
    with tarfile.open(sdists[0]) as t:
        pkg_info = next((m for m in t.getmembers() if m.name.count("/") == 1 and m.name.endswith("/PKG-INFO")), None)
        if pkg_info is None:
            fail("the sdist has no PKG-INFO")
        sdist_version = Parser().parsestr(t.extractfile(pkg_info).read().decode("utf-8"))["Version"]

    if sdist_version != version:
        fail(f"wheel version {version} and sdist version {sdist_version} differ")
    if declared and declared != {version}:
        fail(f"package source declares {sorted(declared)} but the metadata says {version}")
    normalized = re.sub(r"[-_.]+", "_", name).lower()
    for path in wheels + sdists:
        base = os.path.basename(path).lower()
        if not base.startswith(f"{normalized}-{version}".lower()):
            fail(f"{os.path.basename(path)} is not named for {name} {version}")

    if event == "release":
        expected = os.environ.get("RELEASE_TAG", "")
    elif event == "workflow_dispatch" and os.environ.get("TARGET") == "pypi":
        if os.environ.get("REF_TYPE") != "tag":
            fail("a manual production publish must be run from a release tag, not from a branch")
        expected = os.environ.get("REF_NAME", "")
    else:
        expected = None  # TestPyPI or a local run: no tag to compare, the consistency checks above still apply
    if expected is not None and expected != f"v{version}":
        fail(f"release tag {expected!r} does not match the built version v{version}")

    print(f"release gate passed: {name} {version}, commit {sha}, tag {expected or '(none: not a production release)'}")
    for path in wheels + sdists:
        print(f"  {os.path.basename(path)}")


if __name__ == "__main__":
    main(*sys.argv[1:])
