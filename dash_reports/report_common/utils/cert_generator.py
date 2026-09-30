import os
import datetime
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def gen_certs(folder):
    # Generate a private key
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # Create a certificate builder
    builder = x509.CertificateBuilder()

    # Add subject information
    builder = builder.subject_name(x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, u"argus.apollo.com"),
    ]))

    # Add public key
    builder = builder.public_key(private_key.public_key())

    # Add issuer information
    builder = builder.issuer_name(x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, u"argus.apollo.com"),
    ]))

    # Add serial number
    builder = builder.serial_number(x509.random_serial_number())

    # Add validity period
    builder = builder.not_valid_before(datetime.datetime.utcnow())
    builder = builder.not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=365))

    # Sign the certificate
    certificate = builder.sign(private_key=private_key, algorithm=hashes.SHA256(), backend=default_backend())

    # Save the certificate and private key to files
    with open(os.path.join(folder, "certificate.pem"), "wb") as f:
        f.write(certificate.public_bytes(serialization.Encoding.PEM))

    with open(os.path.join(folder, "private_key.pem", "wb")) as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))


if __name__ == '__main__':
    gen_certs('c:/temp')
