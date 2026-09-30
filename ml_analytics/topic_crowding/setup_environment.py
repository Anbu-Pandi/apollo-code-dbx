import subprocess
import sys
import nltk


def setup_environment():
    """Setup the environment by installing required packages and downloading NLTK data."""
    print("Setting up environment...")

    # Install requirements
    print("\nInstalling Python packages...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])

    # Download required NLTK data
    print("\nDownloading NLTK data...")
    required_resources = ['punkt', 'stopwords', 'wordnet', 'omw-1.4']
    for resource in required_resources:
        print(f"Downloading {resource}...")
        nltk.download(resource, quiet=True)

    print("\nSetup completed successfully!")


if __name__ == "__main__":
    setup_environment()