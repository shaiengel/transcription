import shutil
import subprocess
from pathlib import Path


def build():
    build_dir = Path("build")

    # Clean
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir()

    # Install deps to build folder
    subprocess.run([
        "pip", "install", "-r", "requirements.txt",
        "-t", str(build_dir),
        "--platform", "manylinux2014_x86_64",
        "--only-binary=:all:"
    ])

    # Copy source
    shutil.copytree("src/transcription_reviewer", build_dir / "transcription_reviewer")

    # Zip
    shutil.make_archive("lambda_function", "zip", build_dir)


if __name__ == "__main__":
    build()
