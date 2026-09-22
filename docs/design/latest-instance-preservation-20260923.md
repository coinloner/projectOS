# Latest Instance Preservation Baseline

## Instance Identity

- Project: `/Users/coinloner/projectOS/project/wanfa-architecture-protocol-20260923-003652-d81f6b`
- Trace: `tr-ac55c258291e`
- Plan: `run-981096391db0`
- Workflow: `architecture_only`
- Provider/model: `wanfa / gpt-5.6-terra`
- Started: `2026-09-23 00:36:52 +08:00`
- Finished: `2026-09-23 01:08:25 +08:00`
- Result: `failed`

The run completed Requirement, Blueprint, all five ModuleDesign nodes, and the
first ImplementationDesign node. It stopped at
`wi-architecture-implementation-task-storage` after the provider returned:

```text
502 upstream_error
Upstream access forbidden, please contact administrator
```

This is not a completed Architecture closure. It is the newest real execution
record and is used only as the source-preservation baseline.

## Observed Call Chain

```text
scripts/validate_wanfa_architecture.py
  -> Project.create_at
  -> build_container
  -> PlannerService.plan_controlled_workflow
  -> architecture_only Workflow template
  -> GraphRunner.run
  -> RequirementAgent
  -> write/publish requirement
  -> ArchitectureAgent: Blueprint
  -> write_architecture_blueprint
  -> DynamicPlanBuilder: ModuleDesign expansion
  -> ArchitectureAgent: ModuleDesign nodes
  -> write_module_design
  -> DynamicPlanBuilder: ImplementationDesign expansion
  -> ArchitectureAgent: ImplementationDesign nodes
  -> write_implementation_design
  -> Architecture integration
  -> integrate_architecture_designs
  -> Architecture quality gate
```

The final two steps were planned but not reached in this instance.

## Code That Must Be Preserved

The preservation snapshot must include the complete working tree used by the
instance, not only files changed relative to `main`. The primary call-chain
implementation is:

```text
scripts/validate_wanfa_architecture.py
app/bootstrap/runtime.py
app/bootstrap/modules.py
app/project/project.py
app/application/runs.py
app/planner/service.py
app/planner/dynamic_builder.py
app/planner/validator.py
app/workflow/templates.py
app/workflow/template.py
app/workflow/compiler.py
app/orchestration/runner.py
app/orchestration/plan.py
app/orchestration/work_item.py
app/orchestration/retry.py
app/orchestration/trace.py
app/orchestration/task_input.py
app/orchestration/delivery_registry.py
app/execution_context.py
app/agent/base_agent.py
app/agent/requirement_agent.py
app/agent/architecture_agent.py
app/llm/config.py
app/llm/factory.py
app/llm/responses.py
app/tool_manager/gateway.py
app/domain/requirement/service.py
app/domain/requirement/tools.py
app/domain/architecture/design_contract.py
app/domain/architecture/service.py
app/domain/architecture/tools.py
app/artifact/repository.py
app/architecture_execution_config.py
```

The snapshot also preserves the tests, experiment configuration, design
records, and all other tracked working-tree changes present when this baseline
was created. Future integration must compare behavior against this snapshot and
must not reconstruct the implementation from the document list alone.

## Merge Rule

Use the preservation snapshot as one side of a three-way integration with
`main`. Do not replay the files by copying directories over `main`.

The minimum regression gate is:

1. The `architecture_only` plan still expands Blueprint into ModuleDesign
   nodes and accepted ModuleDesigns into ImplementationDesign nodes.
2. WorkItem dependencies and staged artifact refs match the recorded plan.
3. Structured Architecture tools remain the only accepted writers.
4. Commit receipts are verified before a WorkItem completes.
5. Provider failure retries only the current WorkItem.
6. Integration cannot start until every required implementation node completes.
7. Requirement publication and Architecture staged artifacts remain auditable
   through the same Trace.

