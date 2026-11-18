"""Rewrite out/PACKAGE.txt with the current payloads and the dylib they carry."""
import hashlib
import pathlib
import plistlib
import subprocess
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
# `dist/FFMenu.dylib` is the CI artefact (git checkout origin/build -- dist/).
# The Temp path is the old hand-copy location; kept as a fallback so the tool
# still works when someone stages the dylib there.
_CANDIDATES = [
    ROOT / "dist" / "FFMenu.dylib",
    pathlib.Path(r"C:\Users\alokg\AppData\Local\Temp\FFMenu-built.dylib"),
]
DY = next((p for p in _CANDIDATES if p.exists()), _CANDIDATES[0])


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


lines = [
    "dylib sha256 : " + sha(DY),
    "dylib size   : " + str(DY.stat().st_size) + " bytes",
]
try:
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                            text=True, cwd=ROOT).stdout.strip()
    lines.append("source commit: " + commit)
except Exception:                                              # noqa: BLE001
    pass
lines += ["", "each payload: size / app id / framework id / sha256", ""]

for p in sorted(OUT.glob("*.ipa")):
    with zipfile.ZipFile(p) as z:
        app = plistlib.loads(z.read("Payload/FreeFire.app/Info.plist"))
        fw = plistlib.loads(z.read(
            "Payload/FreeFire.app/Frameworks/UnityServices.framework/Info.plist"))
        emb = hashlib.sha256(z.read(
            "Payload/FreeFire.app/Frameworks/UnityServices.framework/UnityServices"
        )).hexdigest()
    lines += [
        p.name,
        f"  {p.stat().st_size} bytes",
        f"  app id       {app.get('CFBundleIdentifier')}",
        f"  framework id {fw.get('CFBundleIdentifier')}",
        f"  embedded     {emb}" + ("  (= dylib sha256)" if emb == sha(DY) else "  ** MISMATCH **"),
        f"  sha256       {sha(p)}",
        "",
    ]

text = "\n".join(lines)
(OUT / "PACKAGE.txt").write_text(text, encoding="utf-8")
print(text)
