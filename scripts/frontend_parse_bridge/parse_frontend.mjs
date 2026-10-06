import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import ts from "typescript";

const EXPECTED_VERSION = "6.0.3";
const PROTOCOL = "PDJS1";
const PROTOCOL_V2 = "PDJS2";
const MAX_REQUEST_BYTES = 12 * 1024 * 1024;
const MAX_FILES = 32;
const MAX_FILE_BYTES = 1024 * 1024;
const MAX_TOTAL_BYTES = 8 * 1024 * 1024;
const MAX_DIAGNOSTICS = 128;
const SENSITIVE_LABEL = /(?:secret|token|password|passwd|credential|authorization|cookie|api[_-]?key)/i;
const SCRIPT_KINDS = new Map([
  [".js", ts.ScriptKind.JS],
  [".jsx", ts.ScriptKind.JSX],
  [".ts", ts.ScriptKind.TS],
  [".tsx", ts.ScriptKind.TSX],
]);
const SCRIPT_KIND_NAMES = new Map([
  [ts.ScriptKind.JS, "JS"],
  [ts.ScriptKind.JSX, "JSX"],
  [ts.ScriptKind.TS, "TS"],
  [ts.ScriptKind.TSX, "TSX"],
]);
const HTTP_VERBS = new Set(["get", "post", "put", "patch", "delete"]);

function fail(code) {
  throw new Error(code);
}

function readBoundedInput() {
  const chunks = [];
  const buffer = Buffer.alloc(64 * 1024);
  let total = 0;
  while (true) {
    const count = fs.readSync(0, buffer, 0, buffer.length, null);
    if (count === 0) break;
    total += count;
    if (total > MAX_REQUEST_BYTES) fail("REQUEST_TOO_LARGE");
    chunks.push(Buffer.from(buffer.subarray(0, count)));
  }
  return Buffer.concat(chunks, total);
}

function safeName(value) {
  if (!value || value.length > 256 || SENSITIVE_LABEL.test(value)) return "<redacted>";
  return value;
}

function locate(sourceFile, start, end) {
  const startPoint = sourceFile.getLineAndCharacterOfPosition(start);
  const endPoint = sourceFile.getLineAndCharacterOfPosition(end);
  return {
    start_line: startPoint.line + 1,
    start_column: startPoint.character + 1,
    end_line: endPoint.line + 1,
    end_column: endPoint.character + 1,
    start_offset: start,
    end_offset: end,
  };
}

function isFunctionNode(node) {
  return ts.isFunctionDeclaration(node)
    || ts.isFunctionExpression(node)
    || ts.isArrowFunction(node)
    || ts.isMethodDeclaration(node)
    || ts.isConstructorDeclaration(node)
    || ts.isGetAccessorDeclaration(node)
    || ts.isSetAccessorDeclaration(node);
}

function explicitName(node, sourceFile) {
  if (node.name && ts.isIdentifier(node.name)) return node.name.text;
  if (ts.isConstructorDeclaration(node)) return "constructor";
  const parent = node.parent;
  if (parent && ts.isVariableDeclaration(parent) && ts.isIdentifier(parent.name)) {
    return parent.name.text;
  }
  if (parent && ts.isPropertyDeclaration(parent) && ts.isIdentifier(parent.name)) {
    return parent.name.text;
  }
  if (parent && ts.isPropertyAssignment(parent) && ts.isIdentifier(parent.name)) {
    return parent.name.text;
  }
  if (parent && ts.isExportAssignment(parent) && !parent.isExportEquals) {
    return "default";
  }
  return "<anonymous>";
}

function hasExportModifier(node) {
  const modifiers = ts.canHaveModifiers(node) ? ts.getModifiers(node) : undefined;
  if (modifiers?.some((modifier) => modifier.kind === ts.SyntaxKind.ExportKeyword)) {
    return true;
  }
  const parent = node.parent;
  if (parent && (ts.isVariableDeclaration(parent) || ts.isVariableDeclarationList(parent))) {
    const list = ts.isVariableDeclarationList(parent) ? parent : parent.parent;
    const statement = list && list.parent;
    const parentModifiers = statement && ts.canHaveModifiers(statement) ? ts.getModifiers(statement) : undefined;
    return Boolean(parentModifiers?.some((modifier) => modifier.kind === ts.SyntaxKind.ExportKeyword));
  }
  return false;
}

function containsJsx(root) {
  let found = false;
  function visit(node) {
    if (found) return;
    if (node !== root && isFunctionNode(node)) return;
    if (ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node) || ts.isJsxFragment(node)) {
      found = true;
      return;
    }
    ts.forEachChild(node, visit);
  }
  visit(root);
  return found;
}

function classHasJsxRender(node) {
  return node.members.some((member) =>
    member.name && ts.isIdentifier(member.name) && member.name.text === "render" && containsJsx(member),
  );
}

function collectBindings(sourceFile) {
  const bindingCounts = new Map();
  const axiosImports = new Set();
  function addBinding(name, importedAxios = false) {
    if (!name || !ts.isIdentifier(name)) return;
    const value = name.text;
    bindingCounts.set(value, (bindingCounts.get(value) || 0) + 1);
    if (importedAxios) axiosImports.add(value);
  }
  function visit(node) {
    if (ts.isImportDeclaration(node) && ts.isStringLiteralLike(node.moduleSpecifier) && node.moduleSpecifier.text === "axios") {
      const clause = node.importClause;
      if (!clause?.isTypeOnly && clause?.name) addBinding(clause.name, true);
      const bindings = clause?.namedBindings;
      if (!clause?.isTypeOnly && bindings && ts.isNamespaceImport(bindings)) addBinding(bindings.name, true);
    } else if (
      ts.isVariableDeclaration(node)
      || ts.isFunctionDeclaration(node)
      || ts.isClassDeclaration(node)
      || ts.isParameter(node)
      || ts.isBindingElement(node)
      || ts.isCatchClause(node)
    ) {
      if (node.name && ts.isIdentifier(node.name)) addBinding(node.name);
      if (ts.isCatchClause(node) && node.variableDeclaration?.name && ts.isIdentifier(node.variableDeclaration.name)) {
        addBinding(node.variableDeclaration.name);
      }
    } else if (ts.isImportDeclaration(node)) {
      const clause = node.importClause;
      if (clause?.name) addBinding(clause.name);
      const bindings = clause?.namedBindings;
      if (bindings && ts.isNamespaceImport(bindings)) addBinding(bindings.name);
      if (bindings && ts.isNamedImports(bindings)) {
        for (const item of bindings.elements) addBinding(item.name);
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  return { bindingCounts, axiosImports };
}

function collectConservativeBindings(sourceFile) {
  const bindingCounts = new Map();
  const zustandCreateImports = new Set();
  function addName(name) {
    if (!name) return;
    if (ts.isIdentifier(name)) {
      bindingCounts.set(name.text, (bindingCounts.get(name.text) || 0) + 1);
      return;
    }
    if (ts.isObjectBindingPattern(name) || ts.isArrayBindingPattern(name)) {
      for (const element of name.elements) {
        if (ts.isBindingElement(element)) addName(element.name);
      }
    }
  }
  function visit(node) {
    if (
      ts.isVariableDeclaration(node)
      || ts.isParameter(node)
      || ts.isFunctionDeclaration(node)
      || ts.isFunctionExpression(node)
      || ts.isClassDeclaration(node)
      || ts.isClassExpression(node)
      || ts.isEnumDeclaration(node)
      || ts.isModuleDeclaration(node)
      || ts.isInterfaceDeclaration(node)
      || ts.isTypeAliasDeclaration(node)
      || ts.isTypeParameterDeclaration(node)
      || ts.isImportEqualsDeclaration(node)
    ) {
      addName(node.name);
    }
    if (ts.isImportDeclaration(node)) {
      const clause = node.importClause;
      if (clause?.name) addName(clause.name);
      const bindings = clause?.namedBindings;
      if (bindings && ts.isNamespaceImport(bindings)) addName(bindings.name);
      if (bindings && ts.isNamedImports(bindings)) {
        for (const item of bindings.elements) addName(item.name);
      }
      if (
        ts.isStringLiteralLike(node.moduleSpecifier)
        && node.moduleSpecifier.text === "zustand"
        && clause
        && !clause.isTypeOnly
        && bindings
        && ts.isNamedImports(bindings)
      ) {
        for (const item of bindings.elements) {
          const importedName = item.propertyName || item.name;
          if (!item.isTypeOnly && importedName.text === "create") {
            zustandCreateImports.add(item.name.text);
          }
        }
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  return { bindingCounts, zustandCreateImports };
}

function directCreateImport(initializer, importedNames) {
  if (!ts.isCallExpression(initializer)) return null;
  const callee = initializer.expression;
  if (ts.isIdentifier(callee) && importedNames.has(callee.text)) return callee.text;
  if (ts.isCallExpression(callee) && ts.isIdentifier(callee.expression)) {
    if (importedNames.has(callee.expression.text)) return callee.expression.text;
  }
  return null;
}

function parseSource(sourceRow, protocol) {
  const extension = path.posix.extname(sourceRow.path).toLowerCase();
  const scriptKind = SCRIPT_KINDS.get(extension);
  if (!scriptKind) fail("UNSUPPORTED_EXTENSION");
  const sourceBytes = Buffer.from(sourceRow.source_base64, "base64");
  if (sourceBytes.toString("base64") !== sourceRow.source_base64) fail("INVALID_BASE64");
  if (sourceBytes.length > MAX_FILE_BYTES) fail("SOURCE_FILE_TOO_LARGE");
  const digest = crypto.createHash("sha256").update(sourceBytes).digest("hex");
  if (digest !== sourceRow.sha256) fail("SOURCE_DIGEST_MISMATCH");
  let sourceText;
  try {
    sourceText = new TextDecoder("utf-8", { fatal: true }).decode(sourceBytes);
  } catch {
    fail("SOURCE_NOT_UTF8");
  }
  const sourceFile = ts.createSourceFile(
    sourceRow.path,
    sourceText,
    ts.ScriptTarget.Latest,
    true,
    scriptKind,
  );
  const scriptKindName = SCRIPT_KIND_NAMES.get(scriptKind);
  const diagnostics = sourceFile.parseDiagnostics || [];
  if (diagnostics.length > 0) {
    return {
      path: sourceRow.path,
      sha256: digest,
      script_kind: scriptKindName,
      status: "PARTIAL",
      facts: [],
      diagnostics: diagnostics.slice(0, MAX_DIAGNOSTICS).map((item) => {
        const point = sourceFile.getLineAndCharacterOfPosition(Math.max(0, item.start || 0));
        return { code: "SYNTAX_ERROR", line: point.line + 1, column: point.character + 1 };
      }),
    };
  }

  const facts = [];
  const bindings = collectBindings(sourceFile);
  const conservativeBindings = protocol === PROTOCOL_V2
    ? collectConservativeBindings(sourceFile)
    : null;
  function addFact(kind, node, fields = {}) {
    const start = node.getStart(sourceFile, false);
    const end = node.getEnd();
    const location = locate(sourceFile, start, end);
    const idSeed = [
      sourceRow.path,
      digest,
      kind,
      location.start_offset,
      location.end_offset,
    ].join("\0");
    const id = "FE_" + crypto.createHash("sha256").update(idSeed, "utf8").digest("hex").slice(0, 24);
    const certainty = kind.endsWith("_CANDIDATE") ? "CANDIDATE" : "SYNTAX";
    facts.push({ id, kind, location, certainty, ...fields });
  }

  function moduleFields(node) {
    if (!node || !ts.isStringLiteralLike(node)) {
      return { specifier_state: "UNRESOLVED", target_state: "UNRESOLVED" };
    }
    return { specifier_state: "REDACTED", target_state: "UNRESOLVED" };
  }

  function symbolFact(node, isClass) {
    const rawName = explicitName(node, sourceFile);
    const name = safeName(rawName);
    const kind = isClass ? "SYMBOL_CLASS" : "SYMBOL_FUNCTION";
    const functionKind = isClass
      ? undefined
      : ts.isArrowFunction(node) ? "arrow"
        : ts.isFunctionExpression(node) ? "expression"
          : ts.isMethodDeclaration(node) ? "method"
            : ts.isConstructorDeclaration(node) ? "constructor"
              : ts.isGetAccessorDeclaration(node) || ts.isSetAccessorDeclaration(node) ? "accessor"
                : "declaration";
    addFact(kind, node, isClass
      ? { name, exported: hasExportModifier(node) }
      : { name, function_kind: functionKind, exported: hasExportModifier(node) });

    if (rawName !== "<anonymous>" && rawName !== "default" && name === rawName && /^[A-Z]/.test(rawName)) {
      if ((!isClass && containsJsx(node.body || node)) || (isClass && classHasJsxRender(node))) {
        addFact("COMPONENT_CANDIDATE", node, {
          name,
          basis: isClass ? "uppercase_class_with_jsx_render" : "uppercase_function_with_jsx",
        });
      }
    }
    if (!isClass && name === rawName && /^use[A-Z0-9]/.test(rawName)) {
      addFact("HOOK_CANDIDATE", node, { name, basis: "function_name_convention" });
    }
  }

  function visit(node) {
    if (ts.isImportDeclaration(node)) {
      addFact("IMPORT", node, {
        form: "static",
        ...moduleFields(node.moduleSpecifier),
      });
    } else if (ts.isExportDeclaration(node)) {
      addFact("EXPORT", node, {
        form: node.moduleSpecifier ? "reexport" : "local",
        ...moduleFields(node.moduleSpecifier),
      });
    } else if (ts.isExportAssignment(node)) {
      addFact("EXPORT", node, {
        form: node.isExportEquals ? "equals" : "default",
        specifier_state: "NOT_APPLICABLE",
        target_state: "UNRESOLVED",
      });
    }

    if (
      ts.isFunctionDeclaration(node)
      || ts.isFunctionExpression(node)
      || ts.isArrowFunction(node)
      || ts.isMethodDeclaration(node)
      || ts.isConstructorDeclaration(node)
      || ts.isGetAccessorDeclaration(node)
      || ts.isSetAccessorDeclaration(node)
    ) {
      symbolFact(node, false);
    } else if (ts.isClassDeclaration(node) || ts.isClassExpression(node)) {
      symbolFact(node, true);
    }

    if (protocol === PROTOCOL_V2 && ts.isVariableDeclaration(node)
        && node.initializer && ts.isIdentifier(node.name)) {
      const createBinding = directCreateImport(
        node.initializer,
        conservativeBindings.zustandCreateImports,
      );
      if (
        createBinding
        && conservativeBindings.bindingCounts.get(createBinding) === 1
      ) {
        addFact("STORE_DECLARATION_CANDIDATE", node, {
          name: safeName(node.name.text),
          exported: hasExportModifier(node),
          basis: "zustand_create_import_call",
        });
      }
    }

    if (ts.isCallExpression(node)) {
      const callee = node.expression;
      if (callee.kind === ts.SyntaxKind.ImportKeyword) {
        addFact("IMPORT", node, {
          form: "dynamic",
          ...moduleFields(node.arguments[0]),
        });
      } else if (
        ts.isPropertyAccessExpression(callee)
        && ts.isIdentifier(callee.expression)
        && callee.expression.text === "globalThis"
        && callee.name.text === "fetch"
        && (bindings.bindingCounts.get("globalThis") || 0) === 0
      ) {
        addFact("API_CALL_CANDIDATE", node, {
          callee: "globalThis.fetch",
          target_state: "UNRESOLVED",
          basis: "global_fetch_call_syntax",
        });
      } else if (
        ts.isPropertyAccessExpression(callee)
        && ts.isIdentifier(callee.expression)
        && bindings.axiosImports.has(callee.expression.text)
        && (bindings.bindingCounts.get(callee.expression.text) || 0) === 1
        && HTTP_VERBS.has(callee.name.text)
      ) {
        addFact("API_CALL_CANDIDATE", node, {
          callee: "axios." + callee.name.text,
          target_state: "UNRESOLVED",
          basis: "axios_import_call_syntax",
        });
      } else if (
        protocol === PROTOCOL_V2
        && ts.isIdentifier(callee)
        && callee.text === "fetch"
        && conservativeBindings.bindingCounts.get("fetch") === undefined
      ) {
        addFact("API_CALL_CANDIDATE", node, {
          callee: "fetch",
          target_state: "UNRESOLVED",
          basis: "bare_fetch_call_syntax",
        });
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);

  facts.sort((left, right) =>
    left.location.start_offset - right.location.start_offset
    || left.kind.localeCompare(right.kind)
    || left.location.end_offset - right.location.end_offset
    || left.id.localeCompare(right.id),
  );
  return {
    path: sourceRow.path,
    sha256: digest,
    script_kind: scriptKindName,
    status: "ANALYZED",
    facts,
    diagnostics: [],
  };
}

function main() {
  if (ts.version !== EXPECTED_VERSION) fail("PARSER_VERSION_MISMATCH");
  const inputBytes = readBoundedInput();
  const request = JSON.parse(inputBytes.toString("utf8"));
  if (
    !request
    || ![PROTOCOL, PROTOCOL_V2].includes(request.protocol)
    || !Array.isArray(request.sources)
    || request.sources.length < 1
    || request.sources.length > MAX_FILES
  ) fail("INVALID_REQUEST");
  let totalBytes = 0;
  const seen = new Set();
  const files = request.sources.map((sourceRow) => {
    if (
      !sourceRow
      || typeof sourceRow.path !== "string"
      || typeof sourceRow.sha256 !== "string"
      || typeof sourceRow.source_base64 !== "string"
      || path.posix.isAbsolute(sourceRow.path)
      || sourceRow.path.split("/").some((part) => part === ".." || part === "")
      || seen.has(sourceRow.path)
    ) fail("INVALID_REQUEST");
    seen.add(sourceRow.path);
    const byteLength = Buffer.from(sourceRow.source_base64, "base64").length;
    totalBytes += byteLength;
    if (byteLength > MAX_FILE_BYTES || totalBytes > MAX_TOTAL_BYTES) fail("INPUT_LIMIT");
    return parseSource(sourceRow, request.protocol);
  });
  const response = {
    protocol: request.protocol,
    parser_version: ts.version,
    files,
  };
  process.stdout.write(JSON.stringify(response));
}

try {
  main();
} catch (error) {
  const code = typeof error?.message === "string" && /^[A-Z0-9_]{1,48}$/.test(error.message)
    ? error.message
    : "PARSER_INTERNAL_ERROR";
  process.stderr.write(code);
  process.exitCode = 2;
}
