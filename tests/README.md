# CEL Conformance Tests

The CEL conformance test suites provide a complementary specification
of the CEL language in the form of executable software. All CEL
implementations which pass the conformance tests should give valid CEL
expressions the same meaning.

As much as possible, the tests are driven by data files. Implementations
are expected to read the `tests/simple/testdata` files as inputs to the
parse, check, and evaluation behaviors of a given implementation. Reference
implementations may be found in the following locations:

* [cel-go conformance](https://github.com/cel-expr/cel-go/tree/master/conformance)
* [cel-cpp conformance](https://github.com/cel-expr/cel-cpp/tree/master/conformance)
* [cel-java conformance](https://github.com/cel-expr/cel-java/tree/main/conformance/src/test/java/dev/cel/conformance)

Each CEL implementation should also have its own unit tests and benchmarks
for testing subcomponents, its API, and other implementation-dependent
features.
