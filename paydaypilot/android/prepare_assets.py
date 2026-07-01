"""Copy the web app (app/static) into the Android project's assets folder.

Stdlib only — run before every Gradle build (CI does this automatically):
    python3 prepare_assets.py
"""

import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.normpath(os.path.join(HERE, "..", "app", "static"))
DST = os.path.join(HERE, "app", "src", "main", "assets")


def main():
    if os.path.isdir(DST):
        shutil.rmtree(DST)
    shutil.copytree(SRC, DST)
    files = sorted(os.listdir(DST))
    print(f"Copied {len(files)} file(s) to assets: {', '.join(files)}")


if __name__ == "__main__":
    main()
