"""Run captured production ending rendering inside the owned native Job."""
import json
import os
import subprocess
from pathlib import Path


def test_actual_ending_renderer_retains_ordinary_labels_and_hides_unearned_acts(tmp_path):
    source = Path(__file__).resolve().parents[1]
    output = Path(os.environ.get('SRD_LITE_RESULT_DIRECTORY', str(tmp_path))) / 'ending-renderer.json'
    run = subprocess.run([r'C:\Program Files\nodejs\node.exe',
        str(source / 'tests/fixtures/test-ending-renderer.cjs'),
        str(source / 'src-tauri/frontend/app.js'), str(output)],
        check=True, capture_output=True, text=True)
    result = json.loads(output.read_text(encoding='utf-8'))
    assert len(result['results']) == 5
    assert all(row['status'] == 'PASS' for row in result['results'])
    assert result['no_browser_no_UI_no_live_calls'] is True
    assert json.loads(run.stdout) == result['results']
