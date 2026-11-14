# Introduction

The Common Expression Language (CEL) is a simple expression language built on
top of [protocol buffer][1] types. This page discusses basic usage. See
[Language Definition](langdef.md) for the reference.

Suppose a type defined as follows (using protocol buffer syntax):

```proto
package google.account;

import "google/protobuf/struct.proto";

message Account {
  oneof {
    string user_id = 1;
    int64 gaia_id = 2;
  }
  string display_name = 3;
  string phone_number = 4;
  repeated string emails = 5;
  google.protobuf.Struct properties = 6;
  ...
}
```

CEL allows for simple computations to be defined on types like this one. For
example, given an instance of the `Account` type assigned to the variable
`account`:

```proto
has(account.user_id) || has(account.gaia_id)    // true if either one is set
size(account.emails) > 0                        // true if emails is non-empty
matches(account.phone_number, "[0-9-]+")        // true if number matches regexp
```

CEL expressions support most operators and functions one would expect when
working with protocol buffers: boolean operators, relations, arithmetic on
numbers, string and byte string operations, and operations to deal with lists
and maps. The full reference of standard operations is described in the
[language definition](langdef.md#standard). Note that applications of CEL may add
more functions and also allow users to define their own.

CEL also supports construction of list, map, and protocol buffer objects. The
following expressions evaluate to true:

```proto
Account{user_id: 'Pokemon'}.user_id == 'Pokemon'
[true,false,true][1] == false
{'blue': 0x000080, 'red': 0xFF0000}['red'] == 0xFF0000
```

CEL expressions are usually type checked, though the language is designed such
that type checking is optional. Moreover, the language can deal with dynamically
typed features of the most recent protocol buffer standard, [proto3][2], like
`google.protobuf.Struct`. Consider the field `account.properties` which has type
`Struct`. This type represents an untyped Object and is similar to JSON's notion
of an Object. Many APIs use this type for representing user defined data which
doesn't have a matching protocol buffer type. Within CEL, these objects support
the same accesses and functions as protocol buffer types:

```proto
has(account.properties.id) && size(account.properties.id) > 0
```

When the expression `account.properties` is evaluated, CEL will automatically
convert the underlying `Struct` representation into a `map<string,
google.protobuf.Value>`, on which the `has` function can be called. And if a
`Value` is accessed, it will be automatically converted into one of the variants
as expressed by its oneof-definitions. In the example, if `id` is defined in the
struct, and its `Value` represents a string, the function `size` on strings will
be called. If, in turn, `Value` represents a list, the function `size` on lists
is called.

If one views CEL coming from dynamic languages, it's natural to think of the
`size` function as taking any value and determining what to do with it based on
the runtime type (which may include throwing an error because the type cannot be
processed). Coming from statically typed languages, it's natural to think of
`size` as an overloaded function which, if resolution is not possible at compile
time, will be deferred to runtime.

Type dependent behavior can also be expressed, since types are first-class
citizens of CEL:

```proto
has(account.properties.id)
  && (type(account.properties.id) == string
     || type(account.properties.id) == list)
```

CEL's default type checker deals with a mixture of dynamic and static typing;
the resulting type system is commonly also called *gradual typing*. Simply
spoken, the type checker tries to figure out as much as it can, and defers
undecidable decisions to the runtime. However, a CEL expression which does not
make use of any of the dynamic features based on `Struct` et.al can be always
fully type checked at compile time.

[1]: https://en.wikipedia.org/wiki/Protocol_Buffers
[2]: https://developers.google.com/protocol-buffers/docs/proto3
