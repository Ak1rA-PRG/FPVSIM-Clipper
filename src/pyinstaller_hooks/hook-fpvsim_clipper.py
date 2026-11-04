# Keep LGPL-covered Python support code outside the executable archive so
# users can replace it along with the external extension modules and DLLs.
module_collection_mode = {'PySide6': 'py', 'shiboken6': 'py'}
