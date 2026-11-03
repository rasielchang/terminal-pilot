"""Verify actual Mach-O loads without mistaking a dylib's own ID for a load."""
import importlib.util
from pathlib import Path
import unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
spec=importlib.util.spec_from_file_location('verify_portable',Path(__file__).resolve().parents[1]/'scripts/verify-portable.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class Dependencies(unittest.TestCase):
    def test_id_is_not_dependency_but_all_load_types_are_checked(self):
        commands='cmd LC_ID_DYLIB\n name /build/wheel/library.dylib (offset 24)\n'
        expected=[]
        for kind,name in [('LC_LOAD_DYLIB','/unbundled/library.dylib'),('LC_LOAD_WEAK_DYLIB','/unbundled/optional.dylib'),
                          ('LC_REEXPORT_DYLIB','@loader_path/with space.dylib'),('LC_LOAD_UPWARD_DYLIB','/usr/lib/libSystem.B.dylib'),
                          ('LC_LAZY_LOAD_DYLIB','@rpath/lazy.dylib')]:
            commands+=f'cmd {kind}\n cmdsize 64\n name {name} (offset 24)\n';expected.append(name)
        self.assertEqual(list(module.loaded_libraries(commands)),expected)

if __name__=='__main__':unittest.main()
