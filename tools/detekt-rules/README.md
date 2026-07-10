# Appwright Detekt rules

`NoPrivateStateRule` rejects Kotlin properties and constructor properties declared `private` or
`protected`. Build the plugin with Gradle and pass its JAR through Detekt's `plugins` option when
Kotlin-backed Appium extensions are introduced. There is no Kotlin production module in v1.
