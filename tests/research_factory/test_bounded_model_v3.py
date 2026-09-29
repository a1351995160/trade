import jsonschema
from chanlun_trader.research_factory.bounded_model_v1 import candidate_output_schema
from chanlun_trader.research_factory.research_capabilities_v1 import capabilities
from chanlun_trader.research_factory.research_rule_strategy_v3 import ResearchRuleStrategyV3


def test_current_public_example_matches_model_schema_and_parser():
    current = capabilities()
    rules = {**current['rules'], 'capability': current['rules']['version']}
    schema = candidate_output_schema(rules)
    proposal = current['examples']['ma_cross']
    jsonschema.validate(proposal, schema)
    assert ResearchRuleStrategyV3(proposal, strategy_id='schema_test').requirements.warmup_sessions == 21
