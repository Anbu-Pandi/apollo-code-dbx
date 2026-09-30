import nltk
import ssl
import os


def download_nltk_data():
    """Download required NLTK data with error handling."""
    print("Setting up NLTK data...")

    try:
        # Try creating an unverified SSL context if default fails
        try:
            _create_unverified_https_context = ssl._create_unverified_context
        except AttributeError:
            pass
        else:
            ssl._create_default_https_context = _create_unverified_https_context

        # Specify download directory (optional)
        # nltk.data.path.append(os.path.join(os.getcwd(), 'nltk_data'))

        # Download required data
        for package in ['punkt', 'stopwords', 'averaged_perceptron_tagger', 'wordnet']:
            try:
                print(f"Downloading {package}...")
                nltk.download(package, quiet=True)
            except Exception as e:
                print(f"Error downloading {package}: {str(e)}")

        print("NLTK setup completed!")

    except Exception as e:
        print(f"Error during NLTK setup: {str(e)}")
        raise


if __name__ == "__main__":
    download_nltk_data()