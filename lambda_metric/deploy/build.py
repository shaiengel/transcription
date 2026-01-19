import shutil
import subprocess
from pathlib import Path


def build():
    build_dir = Path("build")

    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir()

    subprocess.run([
        "pip", "install", "-r", "requirements.txt",
        "-t", str(build_dir),
        "--platform", "manylinux2014_x86_64",
        "--only-binary=:all:"
    ])

    shutil.copytree("src/lambda_metric", build_dir / "lambda_metric")

    shutil.make_archive("lambda_function", "zip", build_dir)


if __name__ == "__main__":
    build()
