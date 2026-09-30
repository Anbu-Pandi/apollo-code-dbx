def test_imports():
    """Test that all required packages are installed correctly."""
    try:
        import pandas
        import numpy
        import sklearn
        import nltk
        import scipy
        from datetime import datetime

        # Test NLTK specifically
        from nltk.tokenize import word_tokenize
        from nltk.corpus import stopwords
        from nltk.stem import WordNetLemmatizer

        print("All imports successful!")
        return True
    except Exception as e:
        print(f"Error during import test: {str(e)}")
        return False


def test_nltk_resources():
    """Test that NLTK resources are available."""
    try:
        import nltk

        # Test each resource
        nltk.data.find('tokenizers/punkt')
        nltk.data.find('corpora/stopwords')
        nltk.data.find('corpora/wordnet')

        # Quick functional test
        from nltk.tokenize import word_tokenize
        text = "Testing NLTK functionality."
        tokens = word_tokenize(text)

        print("NLTK resources test successful!")
        return True
    except Exception as e:
        print(f"Error during NLTK resource test: {str(e)}")
        return False


if __name__ == "__main__":
    print("Testing installation...")
    imports_ok = test_imports()
    nltk_ok = test_nltk_resources()

    if imports_ok and nltk_ok:
        print("\nAll tests passed! The environment is set up correctly.")
    else:
        print("\nSome tests failed. Please check the errors above.")