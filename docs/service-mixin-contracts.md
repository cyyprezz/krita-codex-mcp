# Service mixin contracts

`tests/test_service_mixin_contract.py` protects both `KritaService` hierarchies from MRO
shadowing and cooperative-initialization drift. Service mixins must not duplicate method or
class-attribute names, define `__init__`, or call `super()`.

Cross-mixin calls are discovered from `self.<member>` references whose member is defined by a
different mixin. The manifest records approved caller/provider pairs rather than every method
call, so internal changes within an approved pair do not create test churn. `ServiceContextMixin`
is the explicit shared provider for plugin feature mixins and does not need pair entries.

For a legitimate new dependency, add the directed `(caller, provider)` pair to
`ALLOWED_CROSS_MIXIN_DEPENDENCIES` only after reviewing whether the helper belongs in shared
infrastructure instead. Run the full unit suite; the failure message includes the source location
and `self.<member>` reference for any unapproved coupling. Remove manifest entries when the final
dependency between a pair disappears.
