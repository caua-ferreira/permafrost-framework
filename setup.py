from setuptools import setup, find_packages

setup(
    name="permafrost-framework",
    version="0.1.0",
    description="Plataforma distribuída de compressão inteligente para arquivamento digital de longo prazo",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    author="Permafrost Contributors",
    license="Apache 2.0",
    python_requires=">=3.10",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        "numpy>=1.24.0",
        "pandas>=2.0.0",
        "pyarrow>=12.0.0",
        "zstandard>=0.21.0",
    ],
    extras_require={
        "full": ["lz4>=4.3.0", "brotli>=1.0.9"],
        "dev":  ["pytest>=7.0", "pytest-cov"],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Intended Audience :: Science/Research",
        "License :: OSI Approved :: Apache Software License",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: System :: Archiving :: Compression",
        "Topic :: Database",
    ],
    keywords="compression archival parquet lzma cold-storage permafrost",
)
