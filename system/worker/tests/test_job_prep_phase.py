"""The status window says what an accepted Job is doing before training starts.

Found on the first real Job: the model was being downloaded for minutes and the
window said "학습 진행 중" with no progress, which reads as stuck. The pipeline
now publishes each phase as it begins, and the window shows it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from gshared_worker.dataset_fetch import DatasetRefused
from gshared_worker.escrow import AcceptOutcome, AcceptResult
from gshared_worker.gui.status_presenter import Display, StatusPresenter
from gshared_worker.job_acceptance import Decision, Work
from gshared_worker.job_runtime import RuntimeOutcome, RuntimeState
from gshared_worker.job_workspace import ModelCache
from gshared_worker.runtime_pipeline import RuntimePipeline, RuntimeSettings
from gshared_worker.status_events import (
    JobChanged,
    JobEnd,
    JobEnded,
    JobPhase,
    JobPhaseChanged,
    JobStage,
    StageChanged,
    WorkerStage,
)
from gshared_worker.worker_connection import _settle_runtime

from tests.test_adapter_pipeline import ADAPTER, Harness
from tests.test_execution_context import body
from tests.test_job_progress import a_job, holding
from tests.test_worker_status_window import Recorded, watching

PREPARING = [JobPhase.CONTEXT, JobPhase.MODEL, JobPhase.DATASET]


def phases(sink: Recorded) -> list[JobPhase]:
    return [event.phase for event in sink.of(JobPhaseChanged)]


class TestThePipelinePublishesEachPhase(unittest.TestCase):
    def test_a_job_without_an_adapter(self):
        sink = watching(self)
        Harness(body()).run()

        self.assertEqual(phases(sink), PREPARING + [JobPhase.IMAGE, JobPhase.TRAINING])
        self.assertTrue(all(e.job_id == "123" for e in sink.of(JobPhaseChanged)))

    def test_a_job_with_an_adapter(self):
        sink = watching(self)
        Harness(body(adapter=dict(ADAPTER))).run()

        self.assertEqual(phases(sink), PREPARING + [JobPhase.ADAPTER, JobPhase.IMAGE,
                                                    JobPhase.TRAINING])

    def test_a_phase_that_fails_is_the_last_one_published(self):
        sink = watching(self)

        def refuse():
            raise DatasetRefused("the dataset does not match")

        outcome = Harness(body(), on_dataset=refuse).run()

        self.assertIs(outcome.state, RuntimeState.FAILED)
        self.assertEqual(phases(sink), PREPARING)

    def test_the_model_phase_says_whether_the_weights_are_already_here(self):
        root = Path(tempfile.mkdtemp())
        pipeline = RuntimePipeline(None, "t", "worker-001", object(), RuntimeSettings(
            workspace_root=root / "work", cache_root=root / "cache",
            chain_id=31221, escrow_address="0x" + "11" * 20))
        context = SimpleNamespace(model=SimpleNamespace(
            model_ref="Qwen/Qwen3-0.6B",
            model_revision="c1899de289a04d12100db370d81485cdf75e47ca"))

        self.assertIs(pipeline._model_cached(context), False)

        cache = ModelCache(root / "cache")
        cache.directory_for("Qwen/Qwen3-0.6B",
                            "c1899de289a04d12100db370d81485cdf75e47ca").mkdir(parents=True)
        cache.mark_complete("Qwen/Qwen3-0.6B", "c1899de289a04d12100db370d81485cdf75e47ca")
        self.assertIs(pipeline._model_cached(context), True)

        broken = SimpleNamespace(model=SimpleNamespace(model_ref="../x", model_revision="main"))
        self.assertIsNone(pipeline._model_cached(broken))

    def test_settlement_is_a_phase_too(self):
        sink = watching(self)
        outcome = RuntimeOutcome(RuntimeState.FINISHED, None, "training finished", 0)
        decision = Decision(a_job(), AcceptResult(AcceptOutcome.ACCEPTED, "ok"),
                            Work.RUN, outcome)

        _settle_runtime(decision, holding(), lambda *p: None,
                        SimpleNamespace(start_settlement=lambda job, result: True))

        self.assertEqual(sink.of(JobPhaseChanged),
                         [JobPhaseChanged("123", JobPhase.SETTLING)])


class TestTheWindowShowsIt(unittest.TestCase):
    def setUp(self):
        self.presenter = StatusPresenter(clock=lambda: "12:00:00")
        for event in (StageChanged(WorkerStage.CONNECTED),
                      JobChanged("5", JobStage.ACCEPTING),
                      JobChanged("5", JobStage.WORKING)):
            self.presenter.apply(event)

    def apply(self, *events):
        for event in events:
            self.presenter.apply(event)
        return self.presenter.view()

    def test_before_the_pipeline_says_anything_it_is_as_before(self):
        view = self.presenter.view()

        self.assertIs(self.presenter.display(), Display.RUNNING)
        self.assertEqual(view.job, "작업 5 · 학습 진행 중")

    def test_each_preparation_phase_is_named_and_the_headline_says_preparing(self):
        for phase, label in ((JobPhase.CONTEXT, "실행 정보를 확인하는 중"),
                             (JobPhase.MODEL, "모델을 준비하는 중"),
                             (JobPhase.DATASET, "데이터셋을 내려받는 중"),
                             (JobPhase.ADAPTER, "어댑터를 내려받는 중"),
                             (JobPhase.IMAGE, "실행 이미지를 확인하는 중")):
            with self.subTest(phase=phase):
                view = self.apply(JobPhaseChanged("5", phase))

                self.assertIs(self.presenter.display(), Display.JOB_ASSIGNED)
                self.assertEqual(view.headline, "작업 준비 중")
                self.assertEqual(view.job, f"작업 5 · {label}")
                self.assertEqual(view.progress, "—")

    def test_training_is_running_with_its_percentage(self):
        view = self.apply(JobPhaseChanged("5", JobPhase.MODEL),
                          JobPhaseChanged("5", JobPhase.TRAINING),
                          JobChanged("5", JobStage.WORKING, 43.0))

        self.assertEqual(view.headline, "작업 실행 중")
        self.assertEqual(view.job, "작업 5 · 학습 진행 중")
        self.assertEqual(view.progress, "43%")

    def test_settling_is_named(self):
        view = self.apply(JobPhaseChanged("5", JobPhase.TRAINING),
                          JobPhaseChanged("5", JobPhase.SETTLING))

        self.assertEqual(view.headline, "작업 실행 중")
        self.assertEqual(view.job, "작업 5 · 결과를 올리고 정산하는 중")

    def test_a_first_model_download_is_said_plainly_in_the_log(self):
        self.apply(JobPhaseChanged("5", JobPhase.MODEL, cached=False))

        self.assertIn("모델을 처음 내려받는 중입니다", self.presenter.lines[-1])
        self.assertIn("몇 분", self.presenter.lines[-1])

    def test_a_cached_model_is_not_announced_as_a_download(self):
        self.apply(JobPhaseChanged("5", JobPhase.MODEL, cached=True))

        self.assertNotIn("처음 내려받는", self.presenter.lines[-1])
        self.assertIn("모델을 준비하는 중", self.presenter.lines[-1])

    def test_each_phase_is_logged_once(self):
        before = len(self.presenter.lines)
        self.apply(JobPhaseChanged("5", JobPhase.DATASET),
                   JobPhaseChanged("5", JobPhase.DATASET))

        self.assertEqual(len(self.presenter.lines), before + 1)

    def test_the_phase_is_forgotten_when_the_job_ends(self):
        view = self.apply(JobPhaseChanged("5", JobPhase.MODEL),
                          JobEnded("5", JobEnd.FAILED))

        self.assertIsNone(self.presenter.job_phase)
        self.assertIs(self.presenter.display(), Display.ONLINE)
        self.assertTrue(view.job.startswith("대기 중"))

    def test_the_next_job_does_not_inherit_the_last_ones_phase(self):
        view = self.apply(JobPhaseChanged("5", JobPhase.SETTLING),
                          JobChanged(None, JobStage.IDLE),
                          JobChanged("6", JobStage.ACCEPTING),
                          JobChanged("6", JobStage.WORKING))

        self.assertEqual(view.job, "작업 6 · 학습 진행 중")

    def test_the_event_carries_nothing_but_an_id_a_phase_and_a_flag(self):
        # It crosses a thread into a window: no URL, no path, no token.
        self.assertEqual(set(JobPhaseChanged.__dataclass_fields__),
                         {"job_id", "phase", "cached"})


if __name__ == "__main__":
    unittest.main()
