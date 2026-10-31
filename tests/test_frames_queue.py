import threading
import time

import pytest

from fm.errors import Cancelled, QueueTimeout
from fm.frames_queue import follow_frames, static_frames


def _append_later(path, lines, delay=0.12):
    def run():
        for line in lines:
            time.sleep(delay)
            with open(path, "a") as f:
                f.write(line)
    t = threading.Thread(target=run)
    t.start()
    return t


def test_static_order():
    assert list(static_frames(["a", "b", "c"])) == ["a", "b", "c"]


def test_queue_file_stops_at_end(tmp_path):
    q = tmp_path / "q.txt"
    q.write_text("/x/a.tif\n/x/b c.tif\n#END\n/x/ignored.tif\n")
    assert list(follow_frames(str(q))) == ["/x/a.tif", "/x/b c.tif"]


def test_non_follow_reads_to_eof_including_partial_last_line(tmp_path):
    q = tmp_path / "q.txt"
    q.write_text("/x/a.tif\n/x/b.tif")
    assert list(follow_frames(str(q), follow=False)) == ["/x/a.tif", "/x/b.tif"]


def test_follow_waits_for_growth(tmp_path):
    q = tmp_path / "q.txt"
    q.write_text("")
    t = _append_later(q, ["/x/1.tif\n", "/x/2.tif\n", "#END\n"])
    got = list(follow_frames(str(q), poll=0.02, idle_timeout=5))
    t.join()
    assert got == ["/x/1.tif", "/x/2.tif"]


def test_follow_waits_for_file_to_exist(tmp_path):
    q = tmp_path / "late.txt"
    t = _append_later(q, ["/x/1.tif\n#END\n"], delay=0.2)
    got = list(follow_frames(str(q), poll=0.02, idle_timeout=5))
    t.join()
    assert got == ["/x/1.tif"]


def test_partial_line_is_not_consumed_early(tmp_path):
    q = tmp_path / "q.txt"
    q.write_text("")
    t = _append_later(q, ["/x/1.t", "if\n", "#END\n"])
    got = list(follow_frames(str(q), poll=0.02, idle_timeout=5))
    t.join()
    assert got == ["/x/1.tif"]


def test_abort_directive(tmp_path):
    q = tmp_path / "q.txt"
    q.write_text("/x/a.tif\n#ABORT\n")
    it = follow_frames(str(q))
    assert next(it) == "/x/a.tif"
    with pytest.raises(Cancelled):
        next(it)


def test_cancel_file(tmp_path):
    q = tmp_path / "q.txt"
    q.write_text("")
    c = tmp_path / "cancel"
    c.write_text("")
    with pytest.raises(Cancelled):
        list(follow_frames(str(q), cancel_file=str(c), poll=0.01))


def test_idle_timeout(tmp_path):
    q = tmp_path / "q.txt"
    q.write_text("")
    with pytest.raises(QueueTimeout):
        list(follow_frames(str(q), idle_timeout=0.2, poll=0.02))


def test_comments_and_blank_lines_ignored(tmp_path):
    q = tmp_path / "q.txt"
    q.write_text("\n# note\n/x/a.tif\r\n\n#END\n")
    assert list(follow_frames(str(q))) == ["/x/a.tif"]
