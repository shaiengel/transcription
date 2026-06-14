import shutil
import subprocess
from pathlib import Path


def build():
    build_dir = Path("build")

    # Clean
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir()

    # Install deps to build folder (Linux x86_64 for Lambda)
    subprocess.run([
        "uv", "pip", "install", "-r", "requirements.txt",
        "--target", str(build_dir),
        "--python-platform", "linux",
        "--python-version", "3.12",
    ], check=True)

    # Copy source
    shutil.copytree("src/post_inference", build_dir / "post_inference")

    # Zip
    shutil.make_archive("lambda_function", "zip", build_dir)


if __name__ == "__main__":
    build()
