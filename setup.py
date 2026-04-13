from setuptools import setup

setup(
    name="costaff-cli",
    version="0.2.3",
    description="CoStaff Agent Ecosystem CLI by CoStaff",
    author="Simon Liu",
    py_modules=["costaff"],
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
            "costaff=costaff:app",
        ],
    },
)
