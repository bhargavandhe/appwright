package appwright.detekt

import io.gitlab.arturbosch.detekt.api.CodeSmell
import io.gitlab.arturbosch.detekt.api.Config
import io.gitlab.arturbosch.detekt.api.Debt
import io.gitlab.arturbosch.detekt.api.Entity
import io.gitlab.arturbosch.detekt.api.Issue
import io.gitlab.arturbosch.detekt.api.Rule
import io.gitlab.arturbosch.detekt.api.Severity
import org.jetbrains.kotlin.lexer.KtTokens
import org.jetbrains.kotlin.psi.KtParameter
import org.jetbrains.kotlin.psi.KtProperty

class NoPrivateStateRule(config: Config) : Rule(config) {
    override val issue = Issue(
        id = "NoPrivateState",
        severity = Severity.Defect,
        description = "State properties must not use private or protected visibility.",
        debt = Debt.FIVE_MINS,
    )

    override fun visitProperty(property: KtProperty) {
        super.visitProperty(property)
        if (property.hasModifier(KtTokens.PRIVATE_KEYWORD) ||
            property.hasModifier(KtTokens.PROTECTED_KEYWORD)
        ) {
            report(
                CodeSmell(
                    issue,
                    Entity.from(property),
                    "Property visibility must be public or internal.",
                )
            )
        }
    }

    override fun visitParameter(parameter: KtParameter) {
        super.visitParameter(parameter)
        if (parameter.hasValOrVar() &&
            (parameter.hasModifier(KtTokens.PRIVATE_KEYWORD) ||
                parameter.hasModifier(KtTokens.PROTECTED_KEYWORD))
        ) {
            report(
                CodeSmell(
                    issue,
                    Entity.from(parameter),
                    "Constructor property visibility must be public or internal.",
                )
            )
        }
    }
}
