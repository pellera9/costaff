from setuptools import setup

setup(
    name="mateclaw-cli",
    version="0.2.3",
    description="Mateclaw Agent Ecosystem CLI by Mateclaw",
    author="Simon Liu",
    py_modules=["mateclaw"],
    install_requires=[
        "typer[all]",
        "rich",
        "questionary",
        "python-dotenv",
        "httpx",
        "fastapi",
        "uvicorn",
        "sqlalchemy",
        "cryptography",
        "pyyaml",
        "psutil",
    ],
    entry_points={
        "console_scripts": [
            "mateclaw=mateclaw:app",
        ],
    },
)
