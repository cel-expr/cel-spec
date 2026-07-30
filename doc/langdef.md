# Language Definition

This page constitutes the reference for CEL. For a gentle introduction, see
[Intro](intro.md).

## Overview

In the taxonomy of programming languages, CEL is:

*   **memory-safe:** programs cannot access unrelated memory, such as
    out-of-bounds array indexes or use-after-free pointer dereferences;
*   **side-effect-free:** a CEL program only computes an output from its inputs;
*   **terminating:** CEL programs cannot loop forever;
*   **strongly-typed:** values have a well-defined type, and operators and
    functions check that their arguments have the expected types;
*   **dynamically-typed:** types are associated with values, not with variables
    or expressions, and type safety is enforced at runtime;
*   **gradually-typed:** an optional type-checking phase before runtime can
    detect and reject some programs which would violate type constraints.

## Syntax

The grammar of CEL is defined below, using `|` for alternatives, `[]` for
optional, `{}` for repeated, and `()` for grouping.

```grammar
Expr           = ConditionalOr ["?" ConditionalOr ":" Expr] ;
ConditionalOr  = [ConditionalOr "||"] ConditionalAnd ;
ConditionalAnd = [ConditionalAnd "&&"] Relation ;
Relation       = [Relation Relop] Addition ;
Relop          = "<" | "<=" | ">=" | ">" | "==" | "!=" | "in" ;
Addition       = [Addition ("+" | "-")] Multiplication ;
Multiplication = [Multiplication ("*" | "/" | "%")] Unary ;
Unary          = Member
               | "!" {"!"} Member
               | "-" {"-"} Member
               ;
Member         = Primary
               | Member "." SELECTOR ["(" [ExprList] ")"]
               | Member "[" Expr "]"
               ;
Primary        = ["."] IDENT ["(" [ExprList] ")"]
               | "(" Expr ")"
               | "[" [ExprList] [","] "]"
               | "{" [MapInits] [","] "}"
               | ["."] SELECTOR { "." SELECTOR } "{" [FieldInits] [","] "}"
               | LITERAL
               ;
ExprList       = Expr {"," Expr} ;
FieldInits     = SELECTOR ":" Expr {"," SELECTOR ":" Expr} ;
MapInits       = Expr ":" Expr {"," Expr ":" Expr} ;
```

Implementations are required to support at least:

*   24-32 repetitions of repeating rules, i.e.:
    *   32 terms separated by `||` in a row;
    *   32 terms separated by `&&` in a row;
    *   32 function call arguments;
    *   list literals with 32 elements;
    *   map or message literals with 32 fields;
    *   24 consecutive ternary conditionals `?:`;
    *   24 binary arithmetic operators of the same precedence in a row;
    *   24 relations in a row.
*   12 repetitions of recursive rules, i.e:
    *   12 nested function calls;
    *   12 selection (`.`) operators in a row;
    *   12 indexing (`[_]`) operators in a row;
    *   12 nested list, map, or message literals.

This grammar corresponds to the following operator precedence and associativity:

Precedence | Operator        | Description                    | Associativity
---------- | --------------- | ------------------------------ | -------------
1          | ()              | Function call                  | Left-to-right
&nbsp;     | .               | Qualified name or field access |
&nbsp;     | []              | Indexing                       |
&nbsp;     | {}              | Field initialization           |
2          | - (unary)       | Negation                       | Right-to-left
&nbsp;     | !               | Logical NOT                    |
3          | *               | Multiplication                 | Left-to-right
&nbsp;     | /               | Division                       |
&nbsp;     | %               | Remainder                      |
4          | +               | Addition                       |
&nbsp;     | - (binary)      | Subtraction                    |
5          | == != < > <= >= | Relations                      |
&nbsp;     | in              | Inclusion test                 |
6          | &&              | Logical AND                    |
7          | \|\|            | Logical OR                     |
8          | ?:              | Conditional                    | Right-to-left

The lexis is defined below. As is typical, the `WHITESPACE` and `COMMENT`
productions are only used to recognize separate lexical elements and are ignored
by the grammar. Please note, that in the lexer `[]` denotes a character range,
`*` represents zero or more, `+` represents one or more, and `?` denotes zero
or one occurrence.

```
IDENT          ::= SELECTOR - RESERVED
SELECTOR       ::= [_a-zA-Z][_a-zA-Z0-9]* - KEYWORD
LITERAL        ::= INT_LIT | UINT_LIT | FLOAT_LIT | STRING_LIT | BYTES_LIT
                 | BOOL_LIT | NULL_LIT
INT_LIT        ::= -? DIGIT+ | -? 0x HEXDIGIT+
UINT_LIT       ::= INT_LIT [uU]
FLOAT_LIT      ::= -? DIGIT* . DIGIT+ EXPONENT? | -? DIGIT+ EXPONENT
DIGIT          ::= [0-9]
HEXDIGIT       ::= [0-9abcdefABCDEF]
EXPONENT       ::= [eE] [+-]? DIGIT+
STRING_LIT     ::= [rR]? ( "    ~( " | \r | \n )*  "
                         | '    ~( ' | \r | \n )*  '
                         | """  ~"""*              """
                         | '''  ~'''*              '''
                         )
BYTES_LIT      ::= [bB] STRING_LIT
ESCAPE         ::= \ [abfnrtv\?"'`]
                 | \ [xX] HEXDIGIT HEXDIGIT
                 | \ u HEXDIGIT HEXDIGIT HEXDIGIT HEXDIGIT
                 | \ U HEXDIGIT HEXDIGIT HEXDIGIT HEXDIGIT HEXDIGIT HEXDIGIT HEXDIGIT HEXDIGIT
                 | \ [0-3] [0-7] [0-7]
BOOL_LIT       ::= "true" | "false"
NULL_LIT       ::= "null"
KEYWORD        ::= BOOL_LIT | NULL_LIT | "in"
RESERVED       ::= "as" | "break" | "const" | "continue" | "else"
                 | "for" | "function" | "if" | "import" | "let"
                 | "loop" | "package" | "namespace" | "return"
                 | "var" | "void" | "while"
WHITESPACE     ::= [\t\n\f\r ]+
COMMENT        ::= '//' ~\n* \n
```

For the sake of a readable representation, the escape
sequences (`ESCAPE`) are kept implicit in string tokens. This means that strings
without the `r` or `R` (raw) prefix process `ESCAPE` sequences, while in strings
with the raw prefix they stay uninterpreted. See documentation of string
literals below.

The following language keywords are reserved and cannot be used as identifiers,
function names, selectors, struct name segments, or field names:

    false in null true

The following tokens are reserved to allow easier embedding of CEL into a host
language and cannot be used as identifiers or function names[^function-names]:

    as break const continue else for function if import let loop package
    namespace return var void while

In general it is a bad idea for those defining contexts or extensions to use
identifiers that are reserved words in programming languages which might embed
CEL.

[^function-names]: Except for receiver-call-style functions, e.g. `a.package()`,
                   which is permitted.

### Name Resolution

A CEL expression is parsed in the scope of a specific protocol buffer package or
message, which controls the interpretation of names. The scope is set by the
application context of an expression. A CEL expression can contain simple names
as in `a` or qualified names as in `a.b`. The meaning of such expressions is a
combination of one or more of:

*   Variables and Functions: some simple names refer to variables in the
    execution context, standard functions, or other name bindings provided by
    the CEL application.
*   Field selection: appending a period and identifier to an expression could
    indicate that we're accessing a field within a protocol buffer or map.
*   Protocol buffer package names: a simple or qualified name could represent an
    absolute or relative name in the protocol buffer package namespace. Package
    names must be followed by a message type, or enum constant.
*   Protocol buffer message types and enum constants: following an
    optional protocol buffer package name, a simple or qualified name could
    refer to a message type or an enum constant in the package's namespace.

Resolution works as follows. If `a.b` is a name to be resolved in the context of
a protobuf declaration with scope `A.B`, then resolution is attempted, in order,
as `A.B.a.b`, `A.a.b`, and finally `a.b`. To override this behavior, one can use
`.a.b`; this name will only be attempted to be resolved in the root scope, i.e.
as `a.b`.

If name qualification is mixed with field selection, the longest prefix of the
name which resolves in the current lexical scope is used. For example, if
`a.b.c` resolves to a message declaration, and `a.b` does so as well with `c` a
possible field selection, then `a.b.c` takes priority over the interpretation
`(a.b).c`.

Note: Comprehensions (.exists, .all, etc) introduce new scopes that add
variables as simple identifiers. When resolving a name within a comprehension
body, the name is first compared against the variables declared by the
comprehension. If there is a match, the name resolves to that variable, taking
precedence over the package-based resolution rules above. If not,
resolution proceeds checking for variable matches in parent 
comprehension scopes recursively. Finally the name follows the package
resolution rules above against declarations in the environment. A name with a
leading '.' always resolves in the root scope, bypassing local scopes from
comprehensions.

For example, in `[1].exists(x, x == 1)`, `x` is a local variable which shadows
any identifier named 'x' in ancestor scopes or the package namespace. In 
`[1].exists(x, .x == 1)`, x is the global variable `x`.

## Values

Values in CEL represent any of the following:

Type          | Description
------------- | ---------------------------------------------------------------
`int`         | 64-bit signed integers
`uint`        | 64-bit unsigned integers
`double`      | 64-bit IEEE floating-point numbers
`bool`        | Booleans (`true` or `false`)
`string`      | Strings of Unicode code points
`bytes`       | Byte sequences
`list`        | Lists of values
`map`         | Associative arrays with `int`, `uint`, `bool`, or `string` keys
`null_type`   | The value `null`
message names | Protocol buffer messages
`type`        | Values representing the types in the first column

### Numeric Values

CEL supports only 64-bit integers and 64-bit IEEE double-precision
floating-point. We only support positive, decimal integer literals; negative
integers are produced by the unary negation operator. Note that the integer 7 as
an `int` is a different value than 7 as a `uint`, which would be written `7u`.
Double-precision floating-point is also supported, and the integer 7 would be
written `7.0`, `7e0`, `.700e1`, or any equivalent representation using a decimal
point or exponent.

Note that currently there are no automatic arithmetic conversions for the
numeric types (`int`, `uint`, and `double`). The arithmetic operators typically
contain overloads for arguments of the same numeric type, but not for mixed-type
arguments. Therefore an expression like `1 + 1u` is going to fail to dispatch.
To perform mixed-type arithmetic, use explicit conversion functions such as
`uint(1) + 1u`. Such explicit conversions will maintain their meaning even if
arithmetic conversions are added in the future.

CEL provides no way to control the finer points of floating-point arithmetic,
such as expression evaluation, rounding mode, or exception handling. However,
any two not-a-number values will compare equal even if their underlying
properties are different.

### String and Bytes Values

Strings are valid sequences of Unicode code points. Bytes are arbitrary
sequences of octets (eight-bit data).

Quoted string literals are delimited by either single- or double-quote
characters, where the closing delimiter must match the opening one, and can
contain any unescaped character except the delimiter or newlines (either CR or
LF).

Triple-quoted string literals are delimited by three single-quotes or three
double-quotes, and may contain any unescaped characters except for the delimiter
sequence. Again, the closing delimiter must match the opening one. Triple-quoted
strings may contain newlines.

Both sorts of strings can include escape sequences, described below.

If preceded by an `r` or `R` character, the string is a _raw_ string and does
not interpret escape sequences. Raw strings are useful for expressing strings
which themselves must use escape sequences, such as regular expressions or
program text.

Bytes literals are represented by string literals preceded by a `b` or `B`
character. The bytes literal is the sequence of bytes given by the UTF-8
representation of the string literal. In addition, the octal escape sequence are
interpreted as octet values rather than as Unicode code points. Both raw and
multiline string literals can be used for byte literals.

Escape sequences are a backslash (`` \ ``) followed by one of the following:

*   A punctuation mark representing itself:
    *   `` \ ``: backslash
    *   `?`: question mark
    *   `"`: double quote
    *   `'`: single quote
    *   `` ` ``: backtick
*   A code for whitespace:
    *   `a`: bell
    *   `b`: backspace
    *   `f`: form feed
    *   `n`: line feed
    *   `r`: carriage return
    *   `t`: horizontal tab
    *   `v`: vertical tab
*   A `u` followed by four hexadecimal characters, encoding a Unicode code point
    in the [BMP](https://www.unicode.org/roadmaps/bmp/).
*   A `U` followed by eight hexadecimal characters, encoding a Unicode code
    point (in any plane). Valid only for string literals.
*   A `x` or `X` followed by two hexadecimal characters. For strings, it denotes
    a Unicode code point. For bytes, it represents an octet value.
*   Three octal digits, in the range `000` to `377`. For strings, it denotes a
    Unicode code point. For bytes, it represents an octet value.

All hexadecimal digits in escape sequences are case-insensitive.

Examples:

CEL Literal   | Meaning
------------- | ---------------------------------------------------
`""`          | Empty string
`'""'`        | String of two double-quote characters
`'''x''x'''`  | String of four characters "`x''x`"
`"\""`        | String of one double-quote character
`"\\"`        | String of one backslash character
`r"\\"`       | String of two backslash characters
`b"abc"`      | Byte sequence of 97, 98, 99
`b"ÿ"`        | Sequence of bytes 195 and 191 (UTF-8 of &yuml;)
`b"\303\277"` | Also sequence of bytes 195 and 191
`"\303\277"`  | String of "&Atilde;&iquest;" (code points 195, 191)
`"\377"`      | String of "&yuml;" (code point 255)
`b"\377"`     | Sequence of byte 255 (*not* UTF-8 of &yuml;)
`"\xFF"`      | String of "&yuml;" (code point 255)
`b"\xff"`     | Sequence of byte 255 (*not* UTF-8 of &yuml;)

The following constructions are syntactically invalid and will result in a parse
error:

*   A backslash (`` \ ``) outside of a valid escape sequence, e.g. `\s`.
*   An invalid Unicode code point, e.g. `\u2FE0`.
*   A UTF-16 surrogate code point, even if in a valid UTF-16 surrogate pair,
    e.g. `\uD83D\uDE03` or `\UD83DDE03`.

While strings must be sequences of valid Unicode code points, no Unicode
normalization is attempted on strings, as there are several normal forms, they
can be expensive to convert, and we don't know which is desired. If Unicode
normalization is desired, it should be performed outside of CEL, or done as a
custom extension function.

Likewise, no advanced collation is attempted on strings, as this depends on the
normalization and can be locale-dependent. Strings are simply treated as
sequences of code points and are ordered with lexicographic ordering based on
the numeric value of the code points.

### Aggregate Values

Lists are ordered sequences of values.

Maps are a set of key values, and a mapping from these keys to arbitrary values.
Key values must be an allowed key type: `int`, `uint`, `bool`, or `string`. Thus
maps are the union of what's allowed in protocol buffer maps and JSON objects.

Note that the type checker uses a finer-grained notion of list and map types.
Lists are `list(A)` for the homogeneous type `A` of list elements. Maps are
`map(K, V)` for maps with keys of type `K` and values of type `V`. The type
`dyn` is used for heterogeneous values See
[Gradual Type Checking](#gradual-type-checking). But these constraints are only
enforced within the type checker; at runtime, lists and maps can have
heterogeneous types.

Any protocol buffer message is a CEL value, and each message type is its own CEL
type, represented as its fully-qualified name.

A list can be denoted by the expression `[e1, e2, ..., eN]`, a map by `{ek1:
ev1, ek2: ev2, ..., ekN: evN}`, and a message by `M{f1: e1, f2: e2, ..., fN:
eN}`, where `M` must be a simple or qualified name which resolves to a message
type (see [Name Resolution](#name-resolution)). For a map, the entry keys are
sub-expressions that must evaluate to values of an allowed type (`int`, `uint`,
`bool`, or `string`). For a message, the field names are identifiers. It is an
error to have duplicate keys or field names. The empty list, map, and message
are `[]`, `{}`, and `M{}`, respectively.

See [Field Selection](#field-selection) for accessing elements of lists, maps,
and messages.

### Booleans and Null

CEL has `true` and `false` as the literals for the `bool` type, with the usual
meanings.

The null value is written `null`. It is used in conversion to and from protocol
buffer and JSON data, but otherwise has no built-in meaning in CEL. In
particular, null has its own type (`null_type`) and is not necessarily allowed
where a value of some other type is expected.

### Type Values

Every value in CEL has a runtime type which is itself a value. The standard
function `type(x)` returns the type of expression `x`.

As types are values, those values (`int`, `string`, etc.) also have a type: the
type `type`, which is an expression by itself which in turn also has type
`type`. So

*   `type(1)` evaluates to `int`
*   `type("a")` evaluates to `string`
*   `type(1) == string` evaluates to `false`
*   `type(type(1)) == type(string)` evaluates to `true`

### Abstract Types

A CEL implementation can add new types to the language. These types will be
given names in the same namespace as the other types, but will have no special
support in the language syntax. The only way to construct or use values of these
abstract types is through functions which the implementor must also provide.

Commonly, an abstract type will have a representation as a protocol buffer, so
that it can be stored or transmitted across a network. In this case, the
abstract type will be given the same name as the protocol buffer, which will
prevent CEL programs from being able to use that particular protocol buffer
message type; they will not be able to construct values of that type by message
expressions nor access the message fields. The abstract type remains abstract.

By default, CEL uses `google.protobuf.Timestamp` and `google.protobuf.Duration`
as abstract types. The standard functions provide ways to construct and
manipulate these values, but CEL programs cannot construct them with message
expressions or access their message fields.

### Protocol Buffer Data Conversion

Protocol buffers have a richer range of types than CEL, so Protocol buffer data
is converted to CEL data when read from a message field, and CEL data is
converted in the other direction when initializing a field. In general, protocol
buffer data can be converted to CEL without error, but range errors are possible
in the other direction.

Protocol Buffer Field Type                       | CEL Type
------------------------------------------------ | --------
int32, int64, sint32, sint64, sfixed32, sfixed64 | `int`
uint32, uint64, fixed32, fixed64                 | `uint`
float, double                                    | `double`
bool, string, bytes                              | same
enum E                                           | `int`
repeated                                         | `list`
map<K, V>                                        | `map`
oneof                                            | options expanded individually, at most one is set
message M                                        | M, except for conversions below

Signed integers, unsigned integers, and floating point numbers are converted to
the singular CEL type of the same sort. The CEL type is capable of expressing
the full range of protocol buffer values. When converting from CEL to protocol
buffers, an out-of-range CEL value results in an error.

Boolean, string, and bytes types have identical ranges and are converted without
error.

Protocol buffer enum values are converted to the corresponding `int` value.
Protocol buffer enum fields can accept any signed 32-bit number, values outside
that range will raise an error.

Repeated fields are converted to CEL lists of converted values, preserving the
order. In the other direction, the CEL list elements must be of the right type
and value to be converted to the corresponding protocol buffer type. Similarly,
protocol buffer maps are converted to CEL maps, and CEL map keys and values must
have the right type and value to be converted in the other direction.

Oneof fields are represented by the translation of each of their options as a
separate field, but at most one of these fields will be "set", as detected by
the `has()` macro. See [Macros](#macros).

Since protocol buffer messages are first-class CEL values, message-valued fields
are used without conversion.

Every protocol buffer field has a default value, and there is no semantic
difference between a field set to this default value, and an unset field. For
message fields, their default value is just the unset state, and an unset
message field is distinct from one set to an empty (i.e. all-unset) message.

The `has()` macro (see [Macros](#macros)) tells whether a message field is set
(i.e. not unset, hence not set to the default value). If an unset field is
nevertheless selected, it evaluates to its default value, or if it is a message
field, it evaluates to an empty (i.e. all-unset) message. This allows
expressions to use iterative field selection to examine the state of fields in
deeply nested messages without needing to test whether every intermediate field
is set. (See exception for wrapper types, below.)

### Dynamic Values

CEL automatically converts certain protocol buffer messages in the
`google.protobuf` package to other types.

google.protobuf message | CEL Conversion
----------------------- | --------------
`Any`                   | dynamically converted to the contained message type, or error
`ListValue`             | list of `Value` messages
`Struct`                | map (with string keys, `Value` values)
`Value`                 | dynamically converted to the contained type (null, double, string, bool, `Struct`, or `ListValue`)
wrapper types           | converted as eponymous field type

The wrapper types are `BoolValue`, `BytesValue`, `DoubleValue`, `FloatValue`,
`Int32Value`, `Int64Value`, `NullValue`, `StringValue`, `Uint32Value`, and
`Uint64Value`. Values of these wrapper types are converted to the obvious type.
Additionally, field selection of an unset message field of wrapper type will
evaluate to `null`, instead of the default message. This is an exception to the
usual evaluation of unset message fields.

Note that this implies some cascading conversions. An `Any` message might be
converted to a `Struct`, one of whose `Value`-typed values might be converted to
a `ListValue` of more values, and so on.

Also, note that all of these conversions are dynamic at runtime, so CEL's static
type analysis cannot avoid the possibility of type-related errors in expressions
using these dynamic values.

## JSON Data Conversion

CEL can also work with JSON data. Since there is a natural correspondence of
most CEL data with protocol buffer data, and protocol buffers have a
[defined mapping](https://developers.google.com/protocol-buffers/docs/proto3#json)
to JSON, this creates a natural mapping of CEL to JSON. This creates an exact
bidirectional mapping between JSON types and a subset of CEL data:

JSON Type | CEL Type
--------- | -----------------------------------------------
`null`    | `null`
Boolean   | `bool`
Number    | `double` (except infinities or NaN)
String    | `string`
Array     | `list` of bi-convertible elements
Object    | `map` (with string keys, bi-convertible values)

We define JSON mappings for much of the remainder of CEL data, but note that
this data will not map back in to CEL as the same value:

CEL Data                                               | JSON Data
------------------------------------------------------ | ---------
`int`                                                  | Number if in interoperable range, otherwise decimal String.
`uint`                                                 | Number if in interoperable range, otherwise decimal String.
double infinity                                        | String `"Infinity"` or `"-Infinity"`
double NaN                                             | String "NaN"
`bytes`                                                | String of base64-encoded bytes
message                                                | JSON conversion of protobuf message.
`list` of convertible elements                         | JSON Array of converted values
`list` with a non-convertible element                  | none
`map` with string keys and convertible values          | JSON Object with converted values
`map` with a non-string key or a non-convertible value | none
`type`                                                 | none

The "interoperable" range of integer values is `-(2^53-1)` to `2^53 - 1`.

## Gradual Type Checking

CEL is a dynamically-typed language, meaning that the types of the values of the
variables and expressions might not be known until runtime. However, CEL has an
optional type-checking phase that takes the types declared for all functions and
variables and tries to deduce the type of the expression and of all its
sub-expressions. This is not always possible, due to the dynamic expansion of
certain messages like `Struct`, `Value`, and `Any` (see
[Dynamic Values](#dynamic-values)). However, if a CEL program does not use
dynamically-expanded messages, it can be statically type-checked.

The type checker uses a richer type system than the types of the dynamic values:
lists have a type parameter for the type of the elements, and maps have two
parameters for the types of keys and values, respectively. These richer types
preserve the stronger type guarantees that protocol buffer messages have. We can
infer stronger types from the standard functions, such as accessing list
elements or map fields. However, the `type()` function and dynamic dispatch to
particular function overloads only use the coarser types of the dynamic values.

The type checker also introduces the `dyn` type, which is the union of all other
types. Therefore the type checker could accept a list of heterogeneous values as
`dyn([1, 3.14, "foo"])`, which is given the type `list(dyn)`. The standard
function `dyn` has no effect at runtime, but signals to the type checker that
its argument should be considered of type `dyn`, `list(dyn)`, or a `dyn`-valued
map.

A CEL type checker attempts to identify possible runtime errors (see
[Runtime Errors](#runtime-errors)), particularly `no_matching_overload` and
`no_such_field`, ahead of runtime. It also serves to optimize execution speed
by narrowing down the number of possible matching overloads for a function
call, and by allowing for a more efficient (unboxed) runtime representation of
values.

By construction, a CEL expression that does not use the dynamic features coming
from `Struct`, `Value`, or `Any`, can be fully statically type-checked and all
overloads can be resolved ahead of runtime.

If a CEL expression uses a mixture of dynamic and static features, a type
checker will still attempt to derive as much information as possible and
delegate undecidable type decisions to runtime.

The type checker is an optional phase of evaluation. Running the type checker
does not affect the result of evaluation, it can only reject expressions as
ill-typed in a given typing context.

## Evaluation

For a given evaluation environment, a CEL expression will deterministically
evaluate to either a value or an error. Here are how different expressions are
evaluated:

*   **Literals:** the various kinds of literals (numbers, booleans, strings,
    bytes, and `null`) evaluate to the values they represent.
*   **Variables:** variables are looked up in the binding environment. An
    unbound variable evaluates to an error.
*   **List, Map, and Message expressions:** each sub-expression is evaluated and
    if any sub-expression results in an error, this expression results in an
    error. Otherwise, it results in the list, map, or message of the
    sub-expression results, or an error if one of the values is of the wrong
    type.
*   **Field selection:** see [Field Selection](#field-selection).
*   **Macros:** see [Macros](#macros).
*   **Logical operators:** see [Logical Operators](#logical-operators).
*   **Other operators:** operators are translated into specially-named functions
    and the sub-expressions become their arguments, for instance `e1 + e2`
    becomes `_+_(e1, e2)`, which is then evaluated as a normal function.
*   **Normal functions:** all argument sub-expressions are evaluated and if any
    results in an error, then this expression results in an error. Otherwise,
    the function is identified by its name and dispatched to a particular
    overload based on the types of the sub-expression values. See
    [Functions](#functions).

Because CEL is free of side effects, the order of evaluation among
sub-expressions is not guaranteed. If multiple sub-expressions would evaluate to
errors causing the enclosing expression to evaluate to an error, it will
propagate one or more of the sub-expression errors, but it is not specified
which ones.

### Evaluation Environment

A CEL expression is parsed and evaluated in the scope of a particular protocol
buffer package, which controls name resolution as described above, and a binding
context, which binds identifiers to values, errors, and functions. A given
identifier has different meanings as a function name or as a variable, depending
on the use. For instance in the expression `size(requests) > size`, the first
`size` is a function, and the second is a variable.

The CEL implementation provides mechanisms for adding bindings of variable names
to either values or errors. The implementation will also provide function
bindings for at least all the standard functions listed below.

Where feasible, CEL implementations ensure that a value bound to a variable name
or returned by a custom function conforms to the CEL type declared for that
value or, for dynamic typed values, _a_ CEL type. Where implementations allow
nonconforming values, (e.g. strings with invalid Unicode code points) to be
provided to a CEL program, conformance must be enforced by the application
embedding the CEL program in order to ensure type safety is maintained.

Some implementations might make use of a _context proto_, where a single
protocol buffer message represents all variable bindings: each field in the
message is a binding of the field name to the field value. This provides a
convenient encapsulation of the binding environment.

The evaluation environment can also specify the expected type of the result. If
the expected type is one of the protocol buffer wrapper messages, then CEL will
attempt to convert the result to the wrapper message, or will raise an error if
the conversion fails.

### Runtime Errors

In general, when a runtime error is produced, expression evaluation is
terminated; exceptions to this rule are discussed in
[Logical Operators](#logical-operators) and [Macros](#macros).

CEL provides the following built-in runtime errors:

*   `no_matching_overload`: this function has no overload for the types of the
    arguments.
*   `no_such_field`: a map or message does not contain the desired field.

There is no in-language representation of errors, no generic way to raise them,
and no way to catch or bypass errors, except for the short-circuiting behavior
of the logical operators, and macros.

### Logical Operators

In the conditional operator `e ? e1 : e2`, evaluates to `e1` if `e` evaluates to
`true`, and `e2` if `e` evaluates to `false`. The untaken branch is presumed to
not be executed, though that is an implementation detail.

In the boolean operators `&&` and `||`: if any of their operands uniquely
determines the result (`false` for `&&` and `true` for `||`) the other operand
may or may not be evaluated, and if that evaluation produces a runtime error, it
will be ignored. This makes those operators commutative (in contrast to
traditional boolean short-circuit operators). The rationale for this behavior is
to allow the boolean operators to be mapped to indexed queries, and align better
with SQL semantics.

To get traditional left-to-right short-circuiting evaluation of logical
operators, as in C or other languages (also called "McCarthy Evaluation"), the
expression `e1 && e2` can be rewritten `e1 ? e2 : false`. Similarly, `e1 || e2`
can be rewritten `e1 ? true : e2`.

### Macros

CEL supports a small set of predefined macros. Macro invocations have the same
syntax as function calls, but follow different type checking rules and runtime
semantics than regular functions. An application of CEL opts-in to which macros
to support, selecting from the predefined set of macros. The currently available
macros are:

*   `has(e.f)`: tests whether a field is available. See "Field Selection" below.
*   `e.all(x, p)`: tests whether a predicate holds for all elements of a list
    `e` or keys of a map `e`. Here `x` is a simple identifier to be used in `p`
    which binds to the element or key. The `all()` macro combines per-element
    predicate results with the "and" (`&&`) operator, so if any predicate
    evaluates to `false`, the macro evaluates to `false`, ignoring any errors
    from other predicates.
*   `e.exists(x, p)`: like the `all()` macro, but combines the predicate results
    with the "or" (`||`) operator, so if any predicate evaluates to `true`, the
    macro evaluates to `true`, ignoring any errors from other predicates.
*   `e.exists_one(x, p)`: like the `exists()` macro, but evaluates to `true` only
    if the predicate of exactly one element/key evaluates to `true`, and the
    rest to `false`. Any other combination of boolean results evaluates to
    `false`, and any predicate error causes the macro to raise an error.
*   `e.map(x, t)`:
    *    transforms a list `e` by taking each element `x` to the
         function given by the expression `t`, which can use the variable `x`. For
         instance, `[1, 2, 3].map(n, n * n)` evaluates to `[1, 4, 9]`. Any evaluation
         error for any element causes the macro to raise an error.
    *    transforms a map `e` by taking each key in the map `x` to the function
         given by the expression `t`, which can use the variable `x`. For
         instance, `{'one': 1, 'two': 2}.map(k, k)` evaluates to `['one', 'two']`.
         Any evaluation error for any element causes the macro to raise an error.
*   `e.map(x, p, t)`: Same as the two-arg map but with a conditional `p` filter
    before the value is transformed.
*   `e.filter(x, p)`:
    *    for a list `e`, returns the sublist of all elements `x` which
         evaluate to `true` in the predicate expression `p` (which can use variable
         `x`). For instance, `[1, 2, 3].filter(i, i % 2 > 0)` evaluates to `[1, 3]`.
         If no elements evaluate to `true`, the result is an empty list. Any
         evaluation error for any element causes the macro to raise an error.
    *    for a map `e`, returns the list of all map keys `x` which
         evaluate to `true` in the predicate expression `p` (which can use variable
         `x`). For instance, `{'one': 1, 'two': 2}.filter(k, k == 'one')` evaluates
         to `['one']`. If no elements evaluate to `true`, the result is an empty
         list. Any evaluation error for any element causes the macro to raise an error.

### Field Selection

A field selection expression, `e.f`, can be applied both to messages and to
maps. For maps, selection is interpreted as the field being a string key.

The semantics depends on the type of the result of evaluating expression `e`:

1.  If `e` evaluates to a message and `f` is not declared in this message, the runtime
    error `no_such_field` is raised.
2.  If `e` evaluates to a message and `f` is declared, but the field is not set, the
    default value of the field's type will be produced. Note that this is `null`
    for messages or the according primitive default value as determined by
    proto2 or proto3 semantics.
3.  If `e` evaluates to a map, then `e.f` is equivalent to `e['f']` (where `f`
    is still being used as a meta-variable, e.g. the expression `x.foo` is
    equivalent to the expression `x['foo']` when `x` evaluates to a map).
4.  In all other cases, `e.f` evaluates to an error.

To test for the presence of a field, the boolean-valued macro `has(e.f)` can be
used.

1.  If `e` evaluates to a map, then `has(e.f)` indicates whether the string `f`
    is a key in the map (note that `f` must syntactically be an identifier).
2.  If `e` evaluates to a message and `f` is not a declared field for the
    message, `has(e.f)` raises a  `no_such_field` error.
3.  If `e` evaluates to a protocol buffers version 2 message and `f` is a
    defined field:
    -   If `f` is a repeated field or map field, `has(e.f)` indicates whether
        the field is non-empty.
    -   If `f` is a singular or  oneof field, `has(e.f)` indicates
        whether the field is set.
4.  If `e` evaluates to a protocol buffers version 3 message and `f` is a
    defined field:
    -   If `f` is a repeated field or map field, `has(e.f)` indicates whether
        the field is non-empty.
    -   If `f` is a oneof or singular message field, `has(e.f)` indicates
        whether the field is set.
    -   If `f` is some other singular field, `has(e.f)` indicates whether the
        field's value is its default value (zero for numeric fields, `false` for
        booleans, empty for strings and bytes).
5.  In all other cases, `has(e.f)` evaluates to an error.

## Performance

Since one of the main applications for CEL is for execution of untrusted
expressions with reliable containment, the time and space cost of evaluation
is an essential part of the specification of the language. But we also want to
give considerable freedom in how to implement the language. To balance these
concerns, we specify only the time and space computational complexity of
language constructs and standard functions (see [Functions](#functions)).

CEL applications are responsible for noting the computational complexity of
any extension functions they provide.

### Abstract Sizes

Space and time complexity will be measured in terms of an abstract size
measurement of CEL expressions and values. The size of a CEL value depends on
its type:

*   *string*: The size is its length, i.e. the number of code points, plus a
    constant.
*   *bytes*: The size is its length, i.e. the number of bytes, plus a constant.
*   *list*: The size is the sum of sizes of its entries, plus a constant.
*   *map*: The size is the sum of the key size plus the value size for all of
    its entries, plus a constant.
*   *message*: The size is the sum of the size of all fields, plus a constant.
*   All other values have constant size.

The size of a CEL program is:

*   *string literal*: The size of the resulting value.
*   *bytes literal*: The size of the resulting value.
*   Grammatical aggregates are the sum of the size of their components.
*   Grammatical primitives other than above have constant size.

Thus, the size of a CEL program is bounded by either the length of the source
text string or the bytes of the proto-encoded AST.

The inputs to a CEL expression are the _bindings_ given to the evaluator and
the _literals_ within the expression itself.

### Time Complexity

Unless otherwise noted, the time complexity of an expression is the sum of the
time complexity of its sub-expressions, plus the sum of the sizes of the
sub-expression values, plus a constant.

For instance, an expression `x` has constant time complexity since it has no
sub-expressions.  An expression `x != y` takes time proportional to the sum of
sizes of the bindings of `x` and `y`, plus a constant.

Some functions cost less than this:

*   The conditional expression `_?_:_`, only evaluates one of the alternative
    sub-expressions.
*   For the `size()` function on lists and maps, the time is proportional to
    the length of its input, not its total size (plus the time of the
    sub-expression).
*   The index operator on lists takes constant time (plus the time of the
    sub-expressions).
*   The select operator on messages takes constant time (plus the time of the
    sub-expression).

Some functions take more time than this.  The following functions take time
proportional to the _product_ of their input sizes (plus the time of the
sub-expressions):

*    The index operator on maps.
*    The select operator on maps.
*    The in operator.
*    The `contains`, `startsWith`, `endsWith`, and `matches` functions on
     strings.

See below for the time cost of macros.

Implementations are free to provide a more performant implementation. For
instance, a hashing implementation of maps would make indexing/selection
faster, but we do not require such sophistication from all implementations.

### Space Complexity

Unless otherwise noted, the space complexity of an expression is the sum of the
space complexity of its sub-expressions, plus a constant. The exceptions are:

*   *Literals*: Message, map, and list literals allocate new space for their
    output.
*   *Concatenation*: The `_+_` operator on lists and strings allocate new space
    for their output.

See below for the space cost of macros.

We'll assume that bytes-to-string and string-to-bytes conversions do not need
to allocate new space.

### Macro Performance

Macros can take considerably more time and space than other constructs, and
can lead to exponential behavior when nested or chained.  For instance,

```
[0,1].all(x,
  [0,1].all(x,
    ...
      [0,1].all(x, 1/0)...))
```

takes exponential (in the size of the expression) time to evaluate, while

```
["foo","bar"].map(x, [x+x,x+x]).map(x, [x+x,x+x])...map(x, [x+x,x+x])
```

is exponential in both time and space.

The time and space cost of macros is the cost of the range sub-expression `e`,
plus the following:

*   `has(e.f)`: Space is constant.
    *   If `e` is a map, time is linear in size of `e`.
    *   If `e` is a message, time is constant.
*   `e.all(x, p)`, `e.exists(x, p)`, and `e.exists_one(x, p)`
    *   Time is the sum of the time of `p` for each element of `e`.
    *   Space is constant.
*   `e.map(x, t)`
    *   Time is the sum of time of `t` for each element of `e`.
    *   Space is the sum of space of `t` for each element of `e`, plus a
        constant.
*   `e.filter(x, t)`
    *   Time is the sum of time of `t` for each element of `e`.
    *   Space is the space of `e`.

### Performance Limits

Putting this all together, we can make the following statements about the cost
of evaluation. Let `P` be the non-literal size of the expression, `L` be the
size of the literals, `B` be the size of the bindings, and `I=B+L` be the total
size of the inputs.

*   The macros other than `has()` are the only avenue for exponential
    behavior. This can be curtailed by the implementation allowing applications
    to set limits on the recursion or chaining of macros, or disable them
    entirely.
*   The concatenation operator `_+_` is the only operator that dramatically
    increases the space complexity, with the program `x + x + ... + x` taking
    time and space `O(B * P^2)`.
*   The string-detection functions (`contains()` and friends) yield a boolean
    result, thus cannot be nested to drive exponential or even higher
    polynomial cost.  We can bound the time cost by `O(B^2 * P)`, with a
    limiting case being `x.contains(y) || x.contains(y) || ...`.
*   The map indexing operators yield a smaller result than their input, and
    thus are also limited in their ability to increase the cost. A particularly
    bad case would be an expensive selection that returns a subcomponent that
    contains the majority of the size of the aggregate, resulting in a time
    cost of `O(P * I)`, and see below.
*   Eliminating all of the above and using only default-cost functions, plus
    aggregate literals, time and space are limited `O(P * I)`.
    A limiting time example is `size(x) + size(x) + ...`.
    A limiting time and space example is `[x, x, ..., x]`.

Note that custom function will alter this analysis if they are more expensive
than the default costs.

## Functions

CEL functions have no observable side effects (there may be side effects like
logging or such which are not observable from CEL). The default argument
evaluation strategy for functions is strict, with exceptions from this rule
discussed in [Logical Operators](#logical-operators) and [Macros](#macros).

Functions are specified by a set of overloads. Each overload defines the number
and type of arguments and the type of the result, as well as an opaque
computation. Argument and result types can use type variables to express
overloads which work on lists and maps. At runtime, a matching overload is
selected and the according computation invoked. If no overload matches, the
runtime error `no_matching_overload` is raised (see also
[Runtime Errors](#runtime-errors)). For example, the standard function `size` is
specified by the following overloads:

<table border="1">
  <tr>
   <th rowspan="4">
      size
    </th>
    <td>
      (string) -> int
    </td>
    <td>
      string length
    </td>
  </tr>
  <tr>
    <td>
      (bytes) -> int
    </td>
    <td>
      bytes length
    </td>
  </tr>
  <tr>
    <td>
      (list(A)) -> int
    </td>
    <td>
      list size
    </td>
  </tr>
  <tr>
    <td>
      (map(A, B)) -> int
    </td>
    <td>
      map size
    </td>
  </tr>
</table>

Overloads must have non-overlapping argument types, after erasure of all type
variables (similar as type erasure in Java). Thus an implementation can
implement overload resolution by simply mapping all argument types to a strong
hash.

Operator sub-expressions are treated as calls to specially-named built-in
functions. For instance, the expression `e1 + e2` is dispatched to the function
`_+_` with arguments `e1` and `e2`. Note that since `_+_` is not an identifier,
there would be no way to write this as a normal function call.

See [Standard Definitions](#standard-definitions) for the list of all predefined
functions and operators.

### Extension Functions

It is possible to add extension functions to CEL, which then behave consistently
with standard functions. The mechanism for doing this is implementation
dependent and usually highly curated. For example, an application domain of CEL
can add a new overload to the `size` function above, provided this overload's
argument types do not overlap with any existing overload. For methodological
reasons, CEL does not allow overloading operators.

Like standard functions, extension functions must be free from observable side
effects in order to prevent expressions from having undefined results, since CEL
does not guarantee evaluation order of sub-expressions.

### Receiver Call Style

A function overload can be declared to use receiver call-style, so it must be
called as `e1.f(e2)` instead of `f(e1, e2)`. Overloads with different call
styles are non-overlapping per definition, regardless of their types.

## Standard Definitions

All predefined operators, functions and constants are listed in the table below.
For each symbol, the available overloads are listed. Operator symbols use a
notation like `_+_` where `_` is a placeholder for an argument.

### Equality

Equality (`_==_`) and inequality (`_!=_`) are defined for all types. Inequality
is the logical negation of equality, i.e. `e1 != e2` is the same as
`!(e1 == e2)` for all expressions `e1` and `e2`.

Type-checking asserts that arguments to equality operators must be the same
type. If the argument types differ, the type-checker will raise an error.
However, if at least one argument is dynamically typed, the type-checker
considers all arguments dynamic and defers type-agreement checks to the
interpreter.

The type-checker uses homogeneous equality to surface potential logical errors
during static analysis, but the runtime uses heterogeneous equality with
a definition of [numeric equality](#Numbers) which treats all numeric types as
though they exist on a continuous number line. Semantically, equality would be
expressed within in CEL as follows:

```cel
type(x) in [double, int, uint]
   && type(y) in [double, int, uint] ? numericEquals(x, y)
   : type(x) == type(y) ? x == y
   : false
```

CEL's support for boxed primitives relies on heterogeneous equality to ensure
that comparisons to `null` evaluate to `true` or `false` rather than error.
This behavior is also useful for evaluating JSON data where all numbers may
be provided as `double` or, depending on the underlying JSON implementation,
possibly `int`. This potential discrepancy between how runtimes handle dynamic
data is further motivation for supporting separate behaviors at type-check and
interpretation.

#### Numbers

The numeric types of `int`, `uint`, and `double` are compared as though they
exist on a continuous number line where two numbers `x` and `y` are equal if
`!(x < y || x > y)`. Since it is possible to compare numeric types without
type conversion, CEL uses this definition for `numericEquals` to support
comparison across numeric types.

This property of cross-type numeric equality is essential for supporting
JSON in a way which mostly closely matches user expectations. The following
expressions are equivalent as the type-checker cannot infer the type of the
`json.number` in the expression since it is considered `dyn` typed:

Index into a map:

```
{1: 'hello', 2: 'world'}[json.number]
{1: 'hello', 2: 'world'}[int(json.number)]
```

Set membership test of a json number in a list of integers:

```
json.number in [1, 2, 3]
int(json.number) in [1, 2, 3]
```

The `double` type follows the IEEE 754 standard. Not-a-number (`NaN`) values
compare as unequal, e.g. `NaN == NaN // false` and `NaN != NaN // true`.

#### Lists and Maps

Two `list` values equal if their entries at each ordinal are equal. For lists
`a` and `b` with length `N`, `a == b` is equivalent to:

```
a[0] == b[0] && a[1] == b[1] && ... && a[N-1] == b[N-1]
```

Two `map` values are equal if their entries are the same. For maps `a` and `b`
with keyset `k1, k2, ..., kN`, `a == b` equality is equivalent to:

```
a[k1] == b[k1] && a[k2] == b[k2] && ... && a[kN] == b[kN]
```

In short, when `list` lengths / `map` key sets are the same, and all element
comparisons are `true`, the result is `true`.

#### Protocol Buffers

CEL uses the C++ [`MessageDifferencer::Equals`](https://developers.google.com/protocol-buffers/docs/reference/cpp/google.protobuf.util.message_differencer#MessageDifferencer.Equals.details)
semantics for comparing Protocol Buffer messages across all runtimes. For two
messages to be equal:

- Both messages must share the same type name and `Descriptor` instance;
- Both messages must have the same set fields;
- All primitive typed fields compare equal by value, e.g. `string`, `int64`;
- All elements of `repeated` fields compare in-order as `true`;
- All entries of `map` fields compare order-independently as `true`;
- All fields of `message` and `group` typed fields compare true, with the
  comparison being performed as if by recursion.
- All unknown fields compare `true` using byte equality.

In addition to the publicly documented behaviors for C++ protobuf equality,
there are some implementation behaviors which are important to mention:

- The `double` type follows the IEEE 754 standard where not-a-number (`NaN`)
  values compare as unequal, e.g. `NaN == NaN // false` and
  `NaN != NaN // true`.
- All `google.protobuf.Any` typed fields are unpacked before comparison,
  unless the `type_url` cannot be resolved, in which case the comparison
  falls back to byte equality.

Protocol buffer equality semantics in C++ are generally consistent with CEL's
definition of heterogeneous equality. Note, Java and Go proto equality
implementations do not follow IEEE 754 for `NaN` values and do not unpack
`google.protobuf.Any` values before comparison. These comparison differences
can result in false negatives or false positives; consequently, CEL provides
a uniform definition across runtimes to ensure consistent evaluation across
runtimes.

There is one edge case where CEL and protobuf equality will produce
different results; however, this edge case is sufficiently unlikely that
the difference is acceptable:

```
// Protocol buffer definition
message Msg {
  repeated google.protobuf.Any values;
}

// CEL - Produces `false` according to protobuf equality since the types of
//       Int32Value and FloatValue are not equal.
Msg{values: [google.protobuf.Int32Value{value: 1}]}
  == Msg{values: [google.protobuf.FloatValue{value: 1.0}]}

// CEL - Produces `true` according to CEL equality with well-known
//       protobuf type unwrapping of the list elements within `values`
//       where the list values are unwrapped to CEL numbers and compared
//       using `numericEquals`.
Msg{values: [google.protobuf.Int32Value{value: 1}]}.values
  == Msg{values: [google.protobuf.FloatValue{value: 1.0}]}.values
```

### Ordering

Ordering operators are defined for `int`, `uint`, `double`, `string`, `bytes`,
`bool`, as well as `timestamp` and `duration`. Runtime ordering is also
supported across `int`, `uint`, and `double` for consistency with the runtime
equality definition for numeric types.

Strings and bytes obey lexicographic ordering of the byte values. Because
strings are encoded in UTF-8, strings consequently also obey lexicographic
ordering of their Unicode code points.

The ordering operators obey the usual algebraic properties, i.e. `e1 <= e2`
gives the same result as `!(e1 > e2)` as well as `(e1 < e2) || (e1 == e2)`.

### Overflow

Arithmetic operations raise an error when the results exceed the range of the
integer type (int, uint) or the timestamp or duration type.  An error is also
raised for conversions which exceed the range of the target type.

There are a few additional considerations to keep in mind with respect to
how and when certain types will overflow:

*  Duration values are limited to a single int64 value, or roughly +-290 years.
*  Timestamp values are limited to the range of values which can be serialized
   as a string: ["0001-01-01T00:00:00Z", "9999-12-31T23:59:59.999999999Z"].
*  Double to int conversions are limited to (minInt, maxInt) non-inclusive.

Note, that whether the minimum or maximum integer value will roundtrip successfully
int -> double -> int can be compiler dependent which is the motivation for the
conservative round-tripping behavior.

### Timezones

Timezones are expressed in the following grammar:

```grammar
TimeZone = "UTC" | LongTZ | FixedTZ ;
LongTZ = ? list available at
           https://www.joda.org/joda-time/timezones.html ? ;
FixedTZ = ( "+" | "-" ) Digit Digit ":" Digit Digit ;
Digit = "0" | "1" | ... | "9" ;
```

Fixed timezones are explicit hour and minute offsets from UTC. Long timezone
names are like `Europe/Paris`, `CET`, or `US/Central`.

### Regular Expressions

Regular expressions follow the
[RE2 syntax](https://github.com/google/re2/wiki/Syntax). Regular expression
matches succeed if they match a substring of the argument. Use explicit anchors
(`^` and `$`) in the pattern to force full-string matching, if desired.

### Standard Environment

#### Presence and Comprehension Macros

**has(message.field)** \- Checks if a field exists within a message. This macro
supports proto2, proto3, and map key accesses. Only map accesses using the
select notation are supported.

**Signatures**

*   `has(message.field) -> bool`

**Examples**

```
// `true` if the 'address' field exists in the 'user' message
has(user.address)
// `true` if map 'm' has a key named 'key_name' defined. The value may be null
// as null does not connote absence in CEL.
has(m.key_name)
// `false` if the 'items' field is not set in the 'order' message
has(order.items)
// `false` if the 'user_id' key is not present in the 'sessions' map has(sessions.user_id)
```

**all \-** Tests whether all elements in the input list or all keys in a map
satisfy the given predicate. The all macro behaves in a manner consistent with
the Logical AND operator including in how it absorbs errors and short-circuits.

**Signatures**

*   `list(A).all(A, predicate(A) -> bool) -> bool`
*   `map(A, B).all(A, predicate(A) -> bool) -> bool`

**Examples**

```
[1, 2, 3].all(x, x > 0) // true
[1, 2, 0].all(x, x > 0) // false
['apple', 'banana', 'cherry'].all(fruit, fruit.size() > 3) // true
[3.14, 2.71, 1.61].all(num, num < 3.0) // false
{'a': 1, 'b': 2, 'c': 3}.all(key, key != 'b') // false
```

**exists** \- Tests whether any value in the list or any key in the map
satisfies the predicate expression. The exists macro behaves in a manner
consistent with the Logical OR operator including in how it absorbs errors and
short-circuits.

**Signatures**

*   `list(A).exists(A, predicate(A) -> bool) -> bool`
*   `map(A,B).exists(A, predicate(A) -> bool) -> bool`

**Examples**

```
[1, 2, 3].exists(i, i % 2 != 0) // true
[].exists(i, i > 0) // false
[0, -1, 5].exists(num, num < 0) // true
{'x': 'foo', 'y': 'bar'}.exists(key, key.startsWith('z')) // false
```

**exists\_one** \- Tests whether exactly one list element or map key satisfies
the predicate expression. This macro does not short-circuit in order to remain
consistent with logical operators being the only operators which can absorb
errors within CEL.

**Signatures**

*   `list(A).exists_one(A, predicate(A)) -> bool`
*   `map(A,B).exists_one(A, predicate(A)) -> bool`

**Examples**

```
[1, 2, 2].exists_one(i, i < 2) // true
{'a': 'hello', 'aa': 'hellohello'}.exists_one(k, k.startsWith('a')) // false
[1, 2, 3, 4].exists_one(num, num % 2 == 0) // false
```

**filter** \- Returns a list containing only the elements from the input list
that satisfy the given predicate

**Signatures**

*   `list(A).filter(A, function(A) -> bool) -> list(A)`
*   `map(A, B).filter(A, function(A) -> bool) -> list(A)`

**Examples**

```
[1, 2, 3].filter(x, x > 1) // [2, 3]
['cat', 'dog', 'bird', 'fish'].filter(pet, pet.size() == 3) // ['cat', 'dog']
[{'a': 10, 'b': 5, 'c': 20}].map(m, m.filter(key, m[key] > 10)) // [['c']]
```

**map** \- Returns a list where each element is the result of applying the
transform expression to the corresponding input list element or input map key.

There are two forms of the map macro:

* The three argument form transforms all elements.
* The four argument form transforms only elements which satisfy the predicate.

The four argument form of the macro exists to simplify combined filter / map
operations.

**Signatures**

*   `list(A).map(A, function(A) -> T) -> list(T)`
*   `list(A).map(A, function(A) -> bool, function(A) -> T) -> list(T)`
*   `map(A, B).map(A, function(A) -> T) -> list(T)`
*   `map(A, B).map(A, function(A) -> bool, function(A) -> T) -> list(T)`

**Examples**

```
[1, 2, 3].map(x, x * 2) // [2, 4, 6]
[5, 10, 15].map(x, x / 5) // [1, 2, 3]
['apple', 'banana'].map(fruit, fruit.upperAscii()) // ['APPLE', 'BANANA']
[1, 2, 3, 4].map(num, num % 2 == 0, num * 2) // [4, 8]
```

#### Logical Operators

**Logical NOT (\!)** \- Takes a boolean value as input and returns the opposite
boolean value.

**Signatures:**

*   `!bool -> bool`

**Examples:**

```
!true  // false
!false // true
!error // error
```

**Logical OR (||)** \- Compute the logical OR of two or more values. Errors and
unknown values are considered valid inputs to this operator and will not halt
evaluation.

**Signatures:**

*   `bool || bool -> bool`

**Examples:**

```
true || false  // true
false || false // false
error || true  // true
error || false //  error
```

**Logical AND (&&)** \- Compute the logical AND of two or more values. Errors
and unknown values are considered valid inputs to this operator and will not
halt evaluation.

**Signatures:**

*   `bool && bool -> bool`

**Examples:**

```
true && true   // true
true && false  // false
error && true  // error
error && false // false
```

**Conditional Operator (? : )** \- The conditional or ternary operator which
evaluates the test condition and only one of the remaining sub-expressions

**Signatures:**

*   `bool ? A : A -> A`

**Examples:**

```
true ? 1 : 2 // 1
false ? "a" : "b" // "b"
true ? error : value // error
false ? error : value // value
(2 < 5) ? 'yes' : 'no' // 'yes'
('hello'.size() > 10) ? 1 / 0 : 42 // 42
```

**Note:**

*   `error` is a special value in CEL that represents an error condition.
    Operations involving `error` typically propagate the error.
*   This documentation provides examples for a few CEL operators. The complete
    CEL specification includes many more operators and functions.

#### Arithmetic Operators

**Negation (-)** \- Negate a numeric value.

**Signatures:**

*   `-int -> int`
*   `-double -> double`

**Examples:**

```
-(5)    // -5
-(3.14) // -3.14
```

**Addition (+)** \- Adds two numeric values or concatenates two strings, bytes,
or lists.

**Signatures:**

*   Numeric addition
    *   `int + int -> int`
    *   `uint + uint -> uint`
    *   `double + double -> double`
*   Time and duration addition
    *   `google.protobuf.Timestamp + google.protobuf.Duration ->
        google.protobuf.Timestamp`
    *   `google.protobuf.Duration + google.protobuf.Timestamp ->
        google.protobuf.Timestamp`
    *   `google.protobuf.Duration + google.protobuf.Duration ->
        google.protobuf.Duration`
*   Concatenation
    *   `string + string -> string`
    *   `bytes + bytes -> bytes`
    *   `list(A) + list(A) -> list(A)`

**Examples:**

```
1 + 2 // 3
3.14 + 1.59 // 4.73
"Hello, " + "world!" // "Hello, world!"
[1] + [2, 3] // [1, 2, 3]
duration('1m') + duration('1s') // duration('1m1s')
timestamp('2023-01-01T00:00:00Z')
  +   duration('24h') // timestamp('2023-01-02T00:00:00Z')
```

**Subtraction (-)** \- Subtracts two numeric values or calculates the duration
between two timestamps.

**Signatures:**

*   Numeric subtraction
    *   `int - int -> int`
    *   `uint - uint -> uint`
    *   `double - double -> double`
*   Time and duration subtraction
    *   `google.protobuf.Timestamp - google.protobuf.Timestamp ->
        google.protobuf.Duration`
    *   `google.protobuf.Timestamp - google.protobuf.Duration ->
        google.protobuf.Timestamp`
    *   `google.protobuf.Duration - google.protobuf.Duration ->
        google.protobuf.Duration`

**Examples:**

```
5 - 3 // 2
10.5 - 2.0 // 8.5
duration('1m') - duration('1s') // duration('59s')
timestamp('2023-01-10T12:00:00Z')
  -   timestamp('2023-01-10T00:00:00Z') // duration('12h')
```

**Division (/)** \- Divides two numeric values.

**Signatures:**

*   `int / int -> int`
*   `uint / uint -> uint`
*   `double / double -> double`

**Examples:**

```
10 / 2 // 5
7.0 / 2.0 // 3.5
```

**Modulus (%)** \- Compute the modulus of one integer into another.

**Signatures:**

*   `int % int -> int`
*   `uint % uint -> uint`

**Examples:**

```
3 % 2 // 1
6u % 3u // 0u
```

**Multiply (*)** \- Multiply two numbers. If the operation exceeds the possible
value which can be represented by the type, the result will be an overflow
error.

**Signatures:**

*   `double * double -> double`
*   `int * int -> int`
*   `uint * uint -> uint`

**Examples:**

```
3.5 * 40.0 // 140.0
-2 * 6 // 12
13u * 3u // 39u
```

#### Comparison Operators

Comparisons require strict type equality at type-check time. If types do not
agree, then type-conversion is required in order to be explicit about the
intention and inherent risks of comparing across types.

The one exception to this rule is numeric comparisons at runtime. Since CEL
supports JSON in addition to Protocol Buffers, it must handle cases where the
user intent was to compare an integer value to a JSON value within the int53
range. For this reason, numeric comparisons across type are supported at
runtime as all numeric representations may be considered to exist along a
shared number line independent of their representation in memory.

**Equality (==)** \- Compares two values of the same type and returns `true` if
they are equal, and `false` otherwise

**Signatures:**

*   `A == A -> bool` (where A can be any comparable type)

**Examples:**

```
1 == 1 // true
"hello" == "world" // false
bytes('hello') == b'hello' // true
duration('1h') == duration('60m') // true
dyn(3.0) == 3 // true
```

**Inequality (\!=)** \- Takes two values of the same type and returns `true` if
they are not equal, and `false` otherwise.

**Signatures:**

*   `A != A -> bool` (where A can be any comparable type)

**Examples:**

```
1 != 2     // true
"a" != "a" // false
3.0 != 3.1 // true
```

**Less Than or Equal To (\<=)** \- Compare two values and return `true` if the
first value is less than or equal to the second value.

**Signatures:**

*   `bool <= bool -> bool`
*   `int <= int -> bool`
*   `uint <= uint -> bool`
*   `double <= double -> bool`
*   `string <= string -> bool`
*   `bytes <= bytes -> bool`
*   `google.protobuf.Timestamp <= google.protobuf.Timestamp -> bool`
*   `google.protobuf.Duration <= google.protobuf.Duration -> bool`

**Examples:**

```
2 <= 3 // true
'a' <= 'b' // true
timestamp('2023-08-25T12:00:00Z') <= timestamp('2023-08-26T12:00:00Z') // true
```

**Less Than (\<)** \- Compare two values and return `true` if the first value
is less than the second.

**Signatures:**

*   `bool < bool -> bool`
*   `int < int -> bool`
*   `uint < uint -> bool`
*   `double < double -> bool`
*   `string < string -> bool`
*   `bytes < bytes -> bool`
*   `google.protobuf.Timestamp < google.protobuf.Timestamp -> bool`
*   `google.protobuf.Duration < google.protobuf.Duration -> bool`

**Examples:**

```
2 < 3 // true
'a' < 'b' // true
duration('2h') < duration('3h') // true
-1 < dyn(1u) // true
```

**Greater Than or Equal To (\>=)** \- Compare two values and return `true` if
the first value is greater than or equal to the second.

**Signatures:**

*   `bool >= bool -> bool`
*   `int >= int -> bool`
*   `uint >= uint -> bool`
*   `double >= double -> bool`
*   `string >= string -> bool`
*   `bytes >= bytes -> bool`
*   `google.protobuf.Timestamp >= google.protobuf.Timestamp -> bool`
*   `google.protobuf.Duration >= google.protobuf.Duration -> bool`

**Examples:**

```
3 >= 2 // true
'b' >= 'a' // true
duration('2h') + duration('1h1m') >= duration('3h') // true
1 >= dyn(18446744073709551615u) // false
```

**Greater Than (\>)** \- Compares two values and return `true` if the first
value is greater than the second value

**Signatures:**

*   `bool > bool -> bool`
*   `int > int -> bool`
*   `uint > uint -> bool`
*   `double > double -> bool`
*   `string > string -> bool`
*   `bytes > bytes -> bool`
*   `google.protobuf.Timestamp > google.protobuf.Timestamp -> bool`
*   `google.protobuf.Duration > google.protobuf.Duration -> bool`

**Examples:**

```
3 > 2 // true
'b' > 'a' // true
5u > 3u // true
```

#### List Operators

**List Indexing (\[\])** \- list indexing. Constant time cost

**Signatures:**

*   `list(A)[int] -> A`

**Examples:**

```
[1, 2, 3][1] // 2
```

**List Membership (in)** \- Checks if a value is present in a list. Time cost is
proportional to the product of the size of both arguments.

**Signatures:**

*   `A in list(A) -> bool`

**Examples:**

```
2 in [1, 2, 3] // true
"a" in ["b", "c"] // false
```

**size** \- Determine the number of elements in the list.

**Signatures:**

*   `list.size() -> int`
*   `size(list) -> int`

**Examples:**

```
['hello', 'world'].size() // 2
size(['first', 'second', 'third']) // 3
```

#### Map Operators

**Map Indexing (\[\])** \- map indexing. Expected time complexity is O(1).
Some implementations may not guarantee O(1) lookup times, please check with
the CEL implementation to verify. In the worst case for string keys, the
lookup cost could be proportional to the size of the map keys times the
size of the index string.

**Signatures:**

*   `map(A, B)[A] -> B`

**Examples:**

```
{'key1': 'value1', 'key2': 'value2'}['key1'] // 'value1'
{'name': 'Bob', 'age': 42}['age'] // 42
```

**Map Key Membership (in)** \- Checks if a key exists in a map. Expected time
complexity is O(1).

Some implementations may not guarantee O(1) lookup times, please check with
the CEL implementation to verify. In the worst case for string keys, the
lookup cost could be proportional to the size of the map keys times the
size of the index string.

**Signatures:**

*   `A in map(A, B) -> bool`

**Examples:**

```
'key1' in {'key1': 'value1', 'key2': 'value2'} // true
3 in {1: "one", 2: "two"} // false
```

**size** \- Determine the number of entries in the map.

**Signatures:**

*   `map.size() -> int`
*   `size(map) -> int`

**Examples:**

```
{'hello': 'world'}.size() // 1
size({1: true, 2: false}) // 2
```

#### Bytes Functions

**size** \- Determine the number of bytes in the sequence.

**Signatures:**

*   `bytes.size() -> int`
*   `size(bytes) -> int`

**Examples:**

```
b'hello'.size() // 5
size(b'world!') // 6
size(b'\xF0\x9F\xA4\xAA') // 4
```

#### String Functions

**contains** \- Tests whether the string operand contains the substring. Time
complexity is proportional to the product of the sizes of the arguments.

**Signatures:**

*   `string.contains(string) -> bool`

**Examples:**

```
"hello world".contains("world") // true
"foobar".contains("baz") // false
```

**endsWith** \- Tests whether the string operand ends with the specified suffix.
Average time complexity is linear with respect to the size of the suffix string.
Worst-case time complexity is proportional to the product of the sizes of the
arguments.

**Signatures:**

*   `string.endsWith(string) -> bool`

**Examples:**

```
"hello world".endsWith("world") // true
"foobar".endsWith("bar") // true
```

**matches** \- Tests whether a string matches a given RE2 regular expression.
Time complexity is proportional to the product of the sizes of the arguments
as guaranteed by the [RE2 design](https://github.com/google/re2/wiki/WhyRE2).

**Signatures:**

*   `matches(string, string) -> bool`
*   `string.matches(string) -> bool`

**Examples:**

```
matches("foobar", "foo.*") // true
"foobar".matches("foo.*") // true
```

**startsWith** \- Tests whether the string operand starts with the specified
prefix. Average time complexity is linear with respect to the size of the
prefix. Worst-case time complexity is proportional to the product of the sizes
of the arguments.

**Signatures:**

*   `string.startsWith(string) -> bool`

**Examples:**

```
"hello world".startsWith("hello") // true
"foobar".startsWith("foo") // true
```

**size** \- Determine the length of the string in terms of the number of Unicode
codepoints

**Signatures:**

*   `string.size() -> int`
*   `size(string) -> int`

**Examples:**

```
"hello".size() // 5
size("world!") // 6
"fiance\u0301".size() // 7
size(string(b'\xF0\x9F\xA4\xAA')) // 1
```

#### Date/Time Functions

All timestamp functions which take accept a timezone argument can use any of
the supported [Joda Timezones](https://www.joda.org/joda-time/timezones.html)
either using the numeric format or the geographic region.

**getDate** \- Get the day of the month from a timestamp (one-based indexing).

**Signatures:**

*   `google.protobuf.Timestamp.getDate() -> int` (in UTC)
*   `google.protobuf.Timestamp.getDate(string) -> int` (with timezone)

**Examples:**

```
timestamp("2023-12-25T00:00:00Z").getDate() // 25
timestamp("2023-12-25T00:00:00Z").getDate("America/Los_Angeles") // 24
```

**getDayOfMonth** \- Get the day of the month from a timestamp (zero-based
indexing).

**Signatures:**

*   `google.protobuf.Timestamp.getDayOfMonth() -> int` (in UTC)
*   `google.protobuf.Timestamp.getDayOfMonth(string) -> int` (with timezone)

**Examples:**

```
timestamp("2023-12-25T00:00:00Z").getDayOfMonth() // 24
timestamp("2023-12-25T00:00:00Z").getDayOfMonth("America/Los_Angeles") // 23
```

**getDayOfWeek** \- Get the day of the week from a timestamp (zero-based, zero
for Sunday).

**Signatures:**

*   `google.protobuf.Timestamp.getDayOfWeek() -> int` (in UTC)
*   `google.protobuf.Timestamp.getDayOfWeek(string) -> int` (with timezone)

**Examples:**

```
timestamp("2023-12-25T12:00:00Z").getDayOfWeek() // 1 (Monday)
```

**getDayOfYear** \- Get the day of the year from a timestamp (zero-based
indexing).

**Signatures:**

*   `google.protobuf.Timestamp.getDayOfYear() -> int` (in UTC)
*   `google.protobuf.Timestamp.getDayOfYear(string) -> int` (with timezone)

**Examples:**

```
timestamp("2023-12-25T12:00:00Z").getDayOfYear() // 358
```

**getFullYear** \- Get the year from a timestamp.

**Signatures:**

*   `google.protobuf.Timestamp.getFullYear() -> int` (in UTC)
*   `google.protobuf.Timestamp.getFullYear(string) -> int` (with timezone)

**Examples:**

```
timestamp("2023-12-25T12:00:00Z").getFullYear() // 2023
```

**getHours** \- Get the hour from a timestamp or convert the duration to hours

**Signatures:**

*   `google.protobuf.Timestamp.getHours() -> int` (in UTC)
*   `google.protobuf.Timestamp.getHours(string) -> int` (with timezone)
*   `google.protobuf.Duration.getHours() -> int` convert the duration to hours

**Examples:**

```
timestamp("2023-12-25T12:00:00Z").getHours() // 12
duration("3h").getHours() // 3
```

**getMilliseconds** \- Get the milliseconds from a timestamp or the
milliseconds portion of the duration

**Signatures:**

*   `google.protobuf.Timestamp.getMilliseconds() -> int` obtain the
    milliseconds component of the timestamp in UTC.
*   `google.protobuf.Timestamp.getMilliseconds(string) -> int` obtain the
    milliseconds component with a timezone.
*   `google.protobuf.Duration.getMilliseconds() -> int` obtain the milliseconds
    portion of the duration value. Other time unit functions convert the
    duration to that format; however, this method does not.

**Examples:**

```
timestamp("2023-12-25T12:00:00.500Z").getMilliseconds() // 500
duration("1.234s").getMilliseconds() // 234
```

**getMinutes** \- Get the minutes from a timestamp or convert a duration to
minutes

**Signatures:**

*   `google.protobuf.Timestamp.getMinutes() -> int` get the minutes component
    of a timestamp in UTC.
*   `google.protobuf.Timestamp.getMinutes(string) -> int` get the minutes
    component of a timestamp within a given timezone.
*   `google.protobuf.Duration.getMinutes() -> int` convert the duration to
    minutes.

**Examples:**

```
timestamp("2023-12-25T12:30:00Z").getMinutes() // 30
duration("1h30m").getMinutes() // 90
```

**getMonth** \- Get the month from a timestamp (zero-based, 0 for January).

**Signatures:**

*   `google.protobuf.Timestamp.getMonth() -> int` (in UTC)
*   `google.protobuf.Timestamp.getMonth(string) -> int` (with timezone)

**Examples:**

```
timestamp("2023-12-25T12:00:00Z").getMonth() // 11 (December)
```

**getSeconds** \- Get the seconds from a timestamp or convert the duration to
seconds

**Signatures:**

*   `google.protobuf.Timestamp.getSeconds() -> int` get the seconds component
    of the timestamp in UTC.
*   `google.protobuf.Timestamp.getSeconds(string) -> int` get the seconds
    component of the timestamp with a provided timezone.
*   `google.protobuf.Duration.getSeconds() -> int` convert the duration to
    seconds.

**Examples:**

```
timestamp("2023-12-25T12:30:30Z").getSeconds() // 30
duration("1m30s").getSeconds() // 90
```

#### Types and Conversions

**bool** `type(bool)` \- Type denotation

**Signatures:**

*   `bool(bool) -> bool` (identity)
*   `bool(string) -> bool` (type conversion)

**Examples:**

```
bool(true) // true
bool("true") // true
bool("FALSE") // false
```

**bytes** `type(bytes)` \- Type denotation

**Signatures:**

*   `bytes(bytes) -> bytes` (identity)
*   `bytes(string) -> bytes` (type conversion)

**Examples:**

```
bytes("hello") // b'hello'
bytes("🤪") // b'\xF0\x9F\xA4\xAA'
```

**double** `type(double)` \- Type denotation

**Signatures:**

*   `double(double) -> double` (identity)
*   `double(int) -> double` (type conversion)
*   `double(uint) -> double` (type conversion)
*   `double(string) -> double` (type conversion)

**Examples:**

```
double(3.14) // 3.14
double(10) // 10.0
double("3.14") // 3.14 (if successful, otherwise an error)
```

**duration**

Type conversion for duration values.

Duration strings support decimal values with the following unit suffixes:

* `h` (hour)
* `m` (minute)
* `s` (second)
* `ms` (millisecond)
* `us` or `µs` (microsecond)
* `ns` (nanosecond)

Values are concatenated with their units without a space (`1s`). Values may
include a decimal point (`23.4s`) and can omit either the integer-part
(`.5m`) or fractional part (`30.s`). Fractional nanoseconds (`0.9ns`) are
silently truncated.

Multiple values may be concatenated (`1h34us`) in any order (`15m30s1h`) and
with any repetition (`1s1s1s`) to form a compound value that equals the sum of
its parts.

A zero-length duration may be represented as the unitless (`0`), but this
cannot be concatenated with any other value.

Any duration string may be prefixed with a minus sign (`-`) or an inert plus
sign (`+`), which applies to the duration value after it is computed (in the
case of compound values).

Duration units greater than the hour granularity, such as days or weeks, are not
supported as this would necessitate an understanding of the locale of the
execution context to account for daylight savings time, leap seconds, and
leap years.

**Signatures:**

*   `duration(google.protobuf.Duration) -> google.protobuf.Duration`
*   `duration(string) -> google.protobuf.Duration`

**Examples:**

```
duration("1h30m") // google.protobuf.Duration representing 1 hour and 30 minutes
duration("0") // zero duration value
duration("-1.5h") // minus 90 minutes
```

**dyn** `type(dyn)` \- Type denotation

The `dyn` types does not exist at runtime, but provides a hint to the
type-checker to disable strong type agreement checks.

**Signatures:**

*   `dyn(A) -> dyn` (type conversion) (where A is any type)

**Examples:**

```
dyn(123) // integer 123 marked `dyn` during type-checking
dyn("hello") // string "hello" marked `dyn` during type-checking
```

**int** `type(int)` \- Type denotation

**Signatures:**

*   `int(int) -> int` (identity)
*   `int(uint) -> int` (type conversion)
*   `int(double) -> int` (type conversion, rounds toward zero, errors if out of
    range)
*   `int(string) -> int` (type conversion)
*   `int(google.protobuf.Timestamp) -> int` converts to seconds since Unix
    epoch

**Examples:**

```
int(123) // 123
int(3.14) // 3
int("123") // 123 (if successful, otherwise an error)
```

**list** `type(list(dyn))` \- Type denotation

**map** `type(map(dyn, dyn))` \- Type denotation

**null\_type** `type(null)` \- Type denotation

**string** `type(string)` \- Type denotation

**Signatures:**

*   `string(string) -> string` (identity)
*   `string(bool) -> string` converts `true` to `"true"` and `false` to
    `"false"`
*   `string(int) -> string` converts integer values to base 10 representation
*   `string(uint) -> string` converts unsigned integer values to base 10
    representation
*   `string(double) -> string` converts a double to a string
*   `string(bytes) -> string` converts a byte sequence to a UTF-8 string, errors
    for invalid code points
*   `string(timestamp) -> string` converts a timestamp value to
    [RFC3339](https://datatracker.ietf.org/doc/html/rfc3339) format
*   `string(duration) -> string` converts a duration value to seconds and
     fractional seconds with an 's' suffix

**Examples:**

```
string(123) // "123"
string(123u) // "123u"
string(3.14) // "3.14"
string(b'hello') // "hello"
string(b'\xf0\x9f\xa4\xaa') // "🤪"
string(duration('1m1ms')) // "60.001s"
```

**timestamp**

**Signatures:**

*   `timestamp(google.protobuf.Timestamp) -> google.protobuf.Timestamp`
    (identity)
*   `timestamp(string) -> google.protobuf.Timestamp` (type conversion, according
    to RFC3339)

**Examples:**

```
// google.protobuf.Timestamp representing August 26, 2023 at 12:39 PM PDT
timestamp("2023-08-26T12:39:00-07:00")
```

**type** `type` \- Type denotation

**Signatures:**

*   `type(A) -> type` (returns the type of the value, where A is any type)

**Examples:**

```
type(123) // int
type("hello") // string
```

**uint** `type(uint)` \- Type denotation

**Signatures:**

*   `uint(uint) -> uint` (identity)
*   `uint(int) -> uint` (type conversion)
*   `uint(double) -> uint` (type conversion, rounds toward zero, errors if out
    of range)
*   `uint(string) -> uint` (type conversion)

**Examples:**

```
uint(123) // 123u
uint(3.14) // 3u
uint("123") // 123u (if successful, otherwise an error)
```

## Appendix 1: Legacy Behavior

### Homogeneous Equality

Prior to cel-spec v0.7.0, CEL runtimes only supported homogeneous equality to be
consistent with the homogeneous equality defined by the type-checker. The
original runtime definition for equality is as follows:

```
Equality and inequality are homogeneous; comparing values of different runtime
types results in a runtime error. Thus `2 == 3` is `false`, but `2 == 2.0` is an
error.

For `double`, all not-a-number (`NaN`) values compare equal. This is different
than the usual semantics of floating-point numbers, but it is more consistent
with the usual expectations of reflexivity, and is more compatible with the
usual notions of equality on protocol buffers.
```
