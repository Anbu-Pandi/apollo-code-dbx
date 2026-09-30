from .chat_interface import get_chat_headers, get_section_specific_layout, create_chat_layout
from . import callbacks  # noqa: F401

def get_headers(app):
    return get_chat_headers(app)

def create_layout():
    return create_chat_layout()

def get_layout(section_name):
    return get_section_specific_layout(section_name)
