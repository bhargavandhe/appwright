package appwright.detekt

import io.gitlab.arturbosch.detekt.api.Config
import io.gitlab.arturbosch.detekt.api.RuleSet
import io.gitlab.arturbosch.detekt.api.RuleSetProvider

class AppwrightRuleSetProvider : RuleSetProvider {
    override val ruleSetId = "appwright"

    override fun instance(config: Config) = RuleSet(
        ruleSetId,
        listOf(NoPrivateStateRule(config)),
    )
}
