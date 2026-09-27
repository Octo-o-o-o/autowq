import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('menubar_installer',Path(__file__).resolve().parents[1]/'scripts/install_menubar.py')
installer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class MenubarInstallerTests(unittest.TestCase):
    def test_activation_finishes_without_environment_or_path_type_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'macos/Assets').mkdir(parents=True)
            (root/'macos/Assets/ResearchIcon.png').write_bytes(b'fixture')
            (root/'packaging/macos').mkdir(parents=True)
            (root/'packaging/macos/app_setup.py').write_text('# fixture')
            def run(args,**kwargs):
                if args[0]=='/usr/bin/swiftc': Path(args[args.index('-o')+1]).write_bytes(b'fixture executable')
                return subprocess.CompletedProcess(args,0)
            with patch.object(installer,'ROOT',root), patch.object(installer.subprocess,'run',side_effect=run), patch('pathlib.Path.home',return_value=root), patch.dict('os.environ',{'LC_ALL':'en_US.UTF-8'}), contextlib.redirect_stdout(io.StringIO()) as out:
                installer.install(root/'Applications',True)
            self.assertIn('Installed and login auto-start enabled:',out.getvalue())
            self.assertTrue((root/'Library/LaunchAgents/com.worldquant.wq-menu.plist').exists())
