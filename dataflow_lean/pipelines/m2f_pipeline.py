"""Composable OpenDCAI DataFlow graphs for full book-to-Lean M2F."""

from __future__ import annotations

from dataclasses import dataclass

from ..framework import PipelineABC
from ..operators import (AtomicMathBlockOperator, BlueprintOperator, LeanAuditOperator,
                         NormalizeBookOperator, ProofRepairOperator, StatementAlignmentOperator,
                         StatementCompilationOperator, SplitLargeFilesOperator)


class M2FFromExtractedPipeline(PipelineABC):
    """Start from `vqa_extract_optimized` output and run the complete M2F chain."""

    def __init__(self, storage, *, blueprint, blueprint_verifier, stage1_model, planner, executor,
                 project_root: str, budget=None):
        super().__init__()
        self.storage = storage
        self.normalize = NormalizeBookOperator()
        self.atomic_blocks = AtomicMathBlockOperator(stage1_model)
        self.blueprint = BlueprintOperator(blueprint, blueprint_verifier)
        self.statement_compilation = StatementCompilationOperator(stage1_model, project_root, budget)
        self.statement_alignment = StatementAlignmentOperator(blueprint_verifier)
        self.split_large_files = SplitLargeFilesOperator(stage1_model)
        self.proof_repair = ProofRepairOperator(planner, executor, budget)
        self.audit = LeanAuditOperator()

    def forward(self):
        self.normalize.run(storage=self.storage.step(), input_key="vqa_pair", output_key="document")
        self.atomic_blocks.run(storage=self.storage.step(), input_key="document", output_key="math_items")
        self.blueprint.run(storage=self.storage.step(), input_key="math_items", output_key="blueprints")
        self.statement_compilation.run(storage=self.storage.step(), input_items_key="math_items",
                                       input_blueprints_key="blueprints", output_key="stage1")
        self.statement_alignment.run(storage=self.storage.step(), input_items_key="math_items", input_stage1_key="stage1",
                                     output_key="statement_alignment")
        self.split_large_files.run(storage=self.storage.step(), input_key="stage1", output_key="stage1")
        self.proof_repair.run(storage=self.storage.step(), input_key="stage1", output_key="stage2")
        self.audit.run(storage=self.storage.step(), input_stage1_key="stage1", input_stage2_key="stage2", output_key="audit")


@dataclass
class OptimizedExtractionOperators:
    """The exact operator instances configured by DataFlow's optimized VQA recipe."""
    pdf_merger: object
    mineru_executor: object
    input_formatter: object
    vqa_extractor: object
    llm_output_parser: object
    qa_merger: object
    vqa_formatter: object


class OptimizedPDFBookToLeanPipeline(M2FFromExtractedPipeline):
    """Official optimized PDF/VQA graph followed in-place by the full M2F graph."""

    def __init__(self, storage, extraction: OptimizedExtractionOperators, **m2f):
        super().__init__(storage, **m2f)
        # Assign individually so DataFlow.compile sees and wraps every OperatorABC.
        self.pdf_merger = extraction.pdf_merger
        self.mineru_executor = extraction.mineru_executor
        self.input_formatter = extraction.input_formatter
        self.vqa_extractor = extraction.vqa_extractor
        self.llm_output_parser = extraction.llm_output_parser
        self.qa_merger = extraction.qa_merger
        self.vqa_formatter = extraction.vqa_formatter

    def forward(self):
        self.pdf_merger.run(storage=self.storage.step(), input_pdf_list_key="input_pdf_paths",
                            input_name_key="name", output_pdf_path_key="merged_pdf_path")
        self.mineru_executor.run(storage=self.storage.step(), input_key="merged_pdf_path",
                                 output_key="vqa_markdown_path")
        self.input_formatter.run(storage=self.storage.step(), input_markdown_path_key="vqa_markdown_path",
                                 output_converted_layout_key="converted_vqa_layout_path")
        self.vqa_extractor.run(storage=self.storage.step(), input_path_key="converted_vqa_layout_path",
                               output_path_key="extracted_llm_vqa_path")
        self.llm_output_parser.run(storage=self.storage.step(), input_response_path_key="extracted_llm_vqa_path",
                                   input_converted_layout_path_key="converted_vqa_layout_path", input_name_key="name",
                                   output_qalist_path_key="extracted_vqa_path")
        self.qa_merger.run(storage=self.storage.step(), input_qalist_path_key="extracted_vqa_path",
                           input_name_key="name", output_merged_qalist_path_key="output_merged_vqalist_path",
                           output_merged_md_path_key="output_merged_md_path", output_qa_item_key="vqa_pair")
        self.vqa_formatter.run(storage=self.storage.step(), input_qa_item_key="vqa_pair",
                               output_messages_key="messages", output_images_key="images")
        super().forward()
