import os
import sys
import importlib.util

pkg_dir = os.path.join(os.path.dirname(__file__), '91GDX・252WORKNO-program')
init_py = os.path.join(pkg_dir, '__init__.py')

spec = importlib.util.spec_from_file_location('gdx91', init_py)
mod = importlib.util.module_from_spec(spec)
mod.__path__ = [pkg_dir]
sys.modules['gdx91'] = mod
spec.loader.exec_module(mod)

# force dry-run
sys.argv = ['gdx91', '--dry-run']

from gdx91.cli import main

if __name__ == '__main__':
    main()
