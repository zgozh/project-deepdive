import com.sun.source.tree.ClassTree;
import com.sun.source.tree.AnnotationTree;
import com.sun.source.tree.CompilationUnitTree;
import com.sun.source.tree.ImportTree;
import com.sun.source.tree.MethodTree;
import com.sun.source.tree.PackageTree;
import com.sun.source.tree.Tree;
import com.sun.source.util.JavacTask;
import com.sun.source.util.SourcePositions;
import com.sun.source.util.TreeScanner;
import com.sun.source.util.Trees;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Base64;
import java.util.Collections;
import java.util.Deque;
import java.util.Iterator;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import javax.tools.Diagnostic;
import javax.tools.DiagnosticCollector;
import javax.tools.JavaCompiler;
import javax.tools.JavaFileObject;
import javax.tools.StandardJavaFileManager;
import javax.tools.ToolProvider;

/** Parse-only syntax bridge. It never attributes, generates or loads target code. */
public final class DeepDiveJavaParser {
    private static final int MAX_AST_NODES = 50_000;
    private static final int MAX_FACT_TEXT_BYTES = 4_096;
    private static final Set<String> FRAMEWORK_ANNOTATIONS = new HashSet<>(Arrays.asList(
            "org.springframework.stereotype.Controller",
            "org.springframework.web.bind.annotation.RestController",
            "org.springframework.web.bind.annotation.RequestMapping",
            "org.springframework.web.bind.annotation.GetMapping",
            "org.springframework.web.bind.annotation.PostMapping",
            "org.springframework.web.bind.annotation.PutMapping",
            "org.springframework.web.bind.annotation.PatchMapping",
            "org.springframework.web.bind.annotation.DeleteMapping",
            "javax.persistence.Entity", "javax.persistence.Embeddable",
            "javax.persistence.MappedSuperclass", "jakarta.persistence.Entity",
            "jakarta.persistence.Embeddable", "jakarta.persistence.MappedSuperclass",
            "org.junit.Test", "org.junit.jupiter.api.Test",
            "org.junit.jupiter.api.RepeatedTest", "org.junit.jupiter.api.TestFactory",
            "org.junit.jupiter.params.ParameterizedTest"));

    private DeepDiveJavaParser() { }

    private static String b64(String value) {
        byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
        if (bytes.length > MAX_FACT_TEXT_BYTES) throw new IllegalArgumentException("fact text limit");
        return Base64.getEncoder().encodeToString(bytes);
    }

    private static long lineCount(String source) {
        String normalized = source.replace("\r\n", "\n").replace('\r', '\n');
        long count = 1;
        for (int i = 0; i < normalized.length(); i++) {
            if (normalized.charAt(i) == '\n' && i + 1 < normalized.length()) {
                count++;
            }
        }
        return count;
    }

    private static String typeName(Tree tree) {
        if (tree == null) return "";
        switch (tree.getKind()) {
            case IDENTIFIER:
                return ((com.sun.source.tree.IdentifierTree) tree).getName().toString();
            case MEMBER_SELECT:
                return typeName(((com.sun.source.tree.MemberSelectTree) tree).getExpression())
                        + "." + ((com.sun.source.tree.MemberSelectTree) tree).getIdentifier();
            case PARAMETERIZED_TYPE:
                return typeName(((com.sun.source.tree.ParameterizedTypeTree) tree).getType());
            case ARRAY_TYPE:
                return typeName(((com.sun.source.tree.ArrayTypeTree) tree).getType());
            case ANNOTATED_TYPE:
                return typeName(((com.sun.source.tree.AnnotatedTypeTree) tree).getUnderlyingType());
            case PRIMITIVE_TYPE:
                return tree.toString();
            default:
                return "";
        }
    }

    private static final class TooManyNodes extends RuntimeException { }

    private static final class Owner {
        final String localId;
        final String qualifiedName;

        Owner(String localId, String qualifiedName) {
            this.localId = localId;
            this.qualifiedName = qualifiedName;
        }
    }

    private static final class FactScanner extends TreeScanner<Void, Void> {
        private final int fileIndex;
        private final CompilationUnitTree unit;
        private final SourcePositions positions;
        private final List<String> rows;
        private final Deque<Owner> owners = new ArrayDeque<>();
        private int nodes;
        private boolean omittedAnonymousClassMembers;

        FactScanner(int fileIndex, CompilationUnitTree unit, SourcePositions positions,
                    List<String> rows, String packageName, long sourceLength) {
            this.fileIndex = fileIndex;
            this.unit = unit;
            this.positions = positions;
            this.rows = rows;
            owners.push(new Owner("compilation_unit:0:" + sourceLength, packageName));

            PackageTree packageTree = unit.getPackage();
            if (packageTree != null && !packageName.isEmpty()) {
                addSymbol("package", packageName, packageName, packageTree,
                        owners.peek().localId);
            }
            for (ImportTree importTree : unit.getImports()) {
                long[] span = span(importTree);
                String target = typeName(importTree.getQualifiedIdentifier());
                if (!target.isEmpty()) {
                    String prefix = importTree.isStatic() ? "java-static-import:" : "java-import:";
                    rows.add("IMPORT\t" + fileIndex + "\t" + span[0] + "\t" + span[1]
                            + "\t" + span[2] + "\t" + span[3] + "\t" + b64(prefix + target));
                }
            }
            for (Tree declaration : unit.getTypeDecls()) {
                scan(declaration, null);
            }
        }

        @Override
        public Void scan(Tree node, Void unused) {
            if (node != null && ++nodes > MAX_AST_NODES) throw new TooManyNodes();
            return super.scan(node, unused);
        }

        private long[] span(Tree tree) {
            long start = positions.getStartPosition(unit, tree);
            long end = positions.getEndPosition(unit, tree);
            if (start < 0 || end < start || unit.getLineMap() == null) {
                throw new IllegalArgumentException("invalid source position");
            }
            long endAnchor = Math.max(start, end == start ? end : end - 1);
            long startLine = unit.getLineMap().getLineNumber(start);
            long endLine = unit.getLineMap().getLineNumber(endAnchor);
            if (startLine < 1 || endLine < startLine) throw new IllegalArgumentException("invalid source line");
            return new long[] {startLine, endLine, start, end};
        }

        private String localId(String kind, long[] span) {
            return kind + ":" + span[2] + ":" + span[3];
        }

        private void addSymbol(String kind, String name, String qualifiedName,
                               Tree tree, String parentLocalId) {
            long[] span = span(tree);
            String local = localId(kind, span);
            rows.add("SYMBOL\t" + fileIndex + "\t" + kind + "\t" + b64(name)
                    + "\t" + b64(qualifiedName) + "\t" + span[0] + "\t" + span[1]
                    + "\t" + span[2] + "\t" + span[3] + "\t" + b64(local)
                    + "\t" + b64(parentLocalId));
        }

        private String declarationName(String simpleName) {
            String ownerName = owners.peek().qualifiedName;
            if (ownerName.isEmpty()) return simpleName;
            return ownerName + "." + simpleName;
        }

        private static String classKind(ClassTree tree) {
            String kind = tree.getKind().name();
            if (kind.equals("RECORD")) return "record";
            switch (tree.getKind()) {
                case CLASS: return "class";
                case INTERFACE: return "interface";
                case ENUM: return "enum";
                case ANNOTATION_TYPE: return "annotation";
                default: return "";
            }
        }

        boolean omittedAnonymousClassMembers() {
            return omittedAnonymousClassMembers;
        }

        private void addTypeRelations(ClassTree tree, String sourceLocalId, String sourceName,
                                      boolean isInterface) {
            Tree superclass = tree.getExtendsClause();
            if (superclass != null) addTypeRelation("EXTENDS", sourceLocalId, sourceName, superclass);
            for (Tree implemented : tree.getImplementsClause()) {
                String relationKind = isInterface ? "EXTENDS" : "IMPLEMENTS";
                addTypeRelation(relationKind, sourceLocalId, sourceName, implemented);
            }
        }

        private void addTypeRelation(String relationKind, String sourceLocalId,
                                     String sourceName, Tree type) {
            String target = typeName(type);
            if (target.isEmpty()) return;
            long[] span = span(type);
            rows.add("TYPE\t" + fileIndex + "\t" + relationKind + "\t" + b64(sourceLocalId)
                    + "\t" + b64("java-type:" + target) + "\t" + span[0] + "\t" + span[1]
                    + "\t" + span[2] + "\t" + span[3] + "\t" + b64(sourceName));
        }

        @Override
        public Void visitClass(ClassTree tree, Void unused) {
            String name = tree.getSimpleName().toString();
            String kind = classKind(tree);
            if (name.isEmpty()) {
                omittedAnonymousClassMembers = true;
                return null;
            }
            if (kind.isEmpty()) return null;
            String qualifiedName = declarationName(name);
            long[] span = span(tree);
            String local = localId(kind, span);
            rows.add("SYMBOL\t" + fileIndex + "\t" + kind + "\t" + b64(name)
                    + "\t" + b64(qualifiedName) + "\t" + span[0] + "\t" + span[1]
                    + "\t" + span[2] + "\t" + span[3] + "\t" + b64(local)
                    + "\t" + b64(owners.peek().localId));
            addTypeRelations(tree, local, qualifiedName, tree.getKind() == Tree.Kind.INTERFACE);
            owners.push(new Owner(local, qualifiedName));
            try {
                for (Tree member : tree.getMembers()) scan(member, null);
            } finally {
                owners.pop();
            }
            return null;
        }

        @Override
        public Void visitMethod(MethodTree tree, Void unused) {
            String kind = tree.getReturnType() == null ? "constructor" : "method";
            String name = tree.getName().toString();
            if (kind.equals("constructor")) {
                String ownerName = owners.peek().qualifiedName;
                int separator = ownerName.lastIndexOf('.');
                name = separator < 0 ? ownerName : ownerName.substring(separator + 1);
            }
            String qualifiedName = declarationName(kind.equals("constructor") ? "<init>" : name);
            long[] span = span(tree);
            String local = localId(kind, span);
            rows.add("SYMBOL\t" + fileIndex + "\t" + kind + "\t" + b64(name)
                    + "\t" + b64(qualifiedName) + "\t" + span[0] + "\t" + span[1]
                    + "\t" + span[2] + "\t" + span[3] + "\t" + b64(local)
                    + "\t" + b64(owners.peek().localId));
            owners.push(new Owner(local, qualifiedName));
            try {
                return super.visitMethod(tree, unused);
            } finally {
                owners.pop();
            }
        }
    }

    private static final class AnnotationScanner extends TreeScanner<Void, Void> {
        private final int fileIndex;
        private final CompilationUnitTree unit;
        private final SourcePositions positions;
        private final List<String> rows;
        private final Set<String> localTypeNames = new HashSet<>();
        private final Deque<String> owners = new ArrayDeque<>();
        private final long sourceLength;
        private int nodes;

        AnnotationScanner(int fileIndex, CompilationUnitTree unit, SourcePositions positions,
                          List<String> rows, long sourceLength) {
            this.fileIndex = fileIndex;
            this.unit = unit;
            this.positions = positions;
            this.rows = rows;
            this.sourceLength = sourceLength;
            unit.accept(new TreeScanner<Void, Void>() {
                @Override
                public Void visitClass(ClassTree tree, Void unused) {
                    if (!tree.getSimpleName().toString().isEmpty()) {
                        localTypeNames.add(tree.getSimpleName().toString());
                    }
                    return super.visitClass(tree, unused);
                }
            }, null);
            owners.push("compilation_unit:0:" + sourceLength);
        }

        @Override
        public Void scan(Tree node, Void unused) {
            if (node != null && ++nodes > MAX_AST_NODES) throw new TooManyNodes();
            return super.scan(node, unused);
        }

        private long[] annotationSpan(Tree tree) {
            long start = positions.getStartPosition(unit, tree);
            long end = positions.getEndPosition(unit, tree);
            if (start < 0 || end <= start || end > sourceLength || unit.getLineMap() == null) {
                throw new IllegalArgumentException("invalid annotation source position");
            }
            long line = unit.getLineMap().getLineNumber(start);
            if (line < 1) throw new IllegalArgumentException("invalid annotation source line");
            return new long[] {line, start, end};
        }

        private String lastSegment(String name) {
            int separator = name.lastIndexOf('.');
            return separator < 0 ? name : name.substring(separator + 1);
        }

        private String resolveAnnotation(AnnotationTree annotation) {
            String writtenName = typeName(annotation.getAnnotationType());
            if (FRAMEWORK_ANNOTATIONS.contains(writtenName)) return writtenName;
            if (writtenName.isEmpty() || writtenName.indexOf('.') >= 0
                    || localTypeNames.contains(writtenName)) return null;
            List<String> candidates = new ArrayList<>();
            for (ImportTree importTree : unit.getImports()) {
                if (importTree.isStatic()) continue;
                String importedName = typeName(importTree.getQualifiedIdentifier());
                if (importedName.endsWith(".*")) continue;
                if (lastSegment(importedName).equals(writtenName)) candidates.add(importedName);
            }
            if (candidates.size() == 1 && FRAMEWORK_ANNOTATIONS.contains(candidates.get(0))) {
                return candidates.get(0);
            }
            return null;
        }

        private void emit(Tree declaration, String targetKind) {
            com.sun.source.tree.ModifiersTree modifiers;
            if (declaration instanceof ClassTree) {
                modifiers = ((ClassTree) declaration).getModifiers();
            } else if (declaration instanceof MethodTree) {
                modifiers = ((MethodTree) declaration).getModifiers();
            } else {
                return;
            }
            long start = positions.getStartPosition(unit, declaration);
            long end = positions.getEndPosition(unit, declaration);
            if (start < 0 || end <= start || end > sourceLength) {
                throw new IllegalArgumentException("invalid declaration source position");
            }
            String targetLocalId = targetKind + ":" + start + ":" + end;
            for (AnnotationTree annotation : modifiers.getAnnotations()) {
                String fqn = resolveAnnotation(annotation);
                if (fqn == null) continue;
                long[] span = annotationSpan(annotation);
                rows.add("ANNOTATION\t" + fileIndex + "\t" + b64(fqn)
                        + "\t" + b64(owners.peek()) + "\t" + b64(targetLocalId)
                        + "\t" + span[0] + "\t" + span[1] + "\t" + span[2]);
            }
        }

        @Override
        public Void visitClass(ClassTree tree, Void unused) {
            String kind = FactScanner.classKind(tree);
            String name = tree.getSimpleName().toString();
            if (kind.isEmpty() || name.isEmpty()) return null;
            long start = positions.getStartPosition(unit, tree);
            long end = positions.getEndPosition(unit, tree);
            if (start < 0 || end <= start || end > sourceLength) {
                throw new IllegalArgumentException("invalid declaration source position");
            }
            String localId = kind + ":" + start + ":" + end;
            emit(tree, kind);
            owners.push(localId);
            try {
                return super.visitClass(tree, unused);
            } finally {
                owners.pop();
            }
        }

        @Override
        public Void visitMethod(MethodTree tree, Void unused) {
            if (tree.getReturnType() != null) {
                long start = positions.getStartPosition(unit, tree);
                long end = positions.getEndPosition(unit, tree);
                if (start < 0 || end <= start || end > sourceLength) {
                    throw new IllegalArgumentException("invalid declaration source position");
                }
                emit(tree, "method");
            }
            return super.visitMethod(tree, unused);
        }
    }

    private static String fileRow(int index, String status, long lines, long sourceLength) {
        return "FILE\t" + index + "\t" + status + "\t" + lines + "\t" + sourceLength;
    }

    private static boolean exceedsNodeLimit(CompilationUnitTree unit) {
        final int[] count = {0};
        new TreeScanner<Void, Void>() {
            @Override
            public Void scan(Tree node, Void unused) {
                if (node != null && ++count[0] > MAX_AST_NODES) throw new TooManyNodes();
                return super.scan(node, unused);
            }
        }.scan(unit, null);
        return false;
    }

    public static void main(String[] args) {
        String protocol = "PDJ1";
        int firstPath = 0;
        if (args.length >= 2 && args[0].equals("--protocol")) {
            if (!args[1].equals("PDJ1") && !args[1].equals("PDJ2")) System.exit(2);
            protocol = args[1];
            firstPath = 2;
        }
        int pathCount = args.length - firstPath;
        if (pathCount < 1 || pathCount > 16) System.exit(2);
        JavaCompiler compiler = ToolProvider.getSystemJavaCompiler();
        if (compiler == null) System.exit(3);
        List<File> files = new ArrayList<>();
        List<String> sourceTexts = new ArrayList<>();
        try {
            for (int i = firstPath; i < args.length; i++) {
                Path path = Paths.get(args[i]);
                String name = path.getFileName().toString();
                if (!name.matches("unit-[0-9]+\\.java")) System.exit(4);
                files.add(path.toFile());
                sourceTexts.add(new String(Files.readAllBytes(path), StandardCharsets.UTF_8));
            }
            System.out.println(protocol);
            for (int i = 0; i < files.size(); i++) {
                String source = sourceTexts.get(i);
                long length = source.length();
                long lines = lineCount(source);
                DiagnosticCollector<JavaFileObject> diagnostics = new DiagnosticCollector<>();
                try (StandardJavaFileManager manager = compiler.getStandardFileManager(
                        diagnostics, Locale.ROOT, StandardCharsets.UTF_8)) {
                    Iterable<? extends JavaFileObject> fileObjects =
                            manager.getJavaFileObjectsFromFiles(Collections.singletonList(files.get(i)));
                    JavacTask task = (JavacTask) compiler.getTask(
                            null, manager, diagnostics,
                            Arrays.asList("-proc:none", "-encoding", "UTF-8", "-Xlint:none"),
                            null, fileObjects);
                    Iterator<? extends CompilationUnitTree> parsedUnits = task.parse().iterator();
                    CompilationUnitTree unit = parsedUnits.hasNext() ? parsedUnits.next() : null;
                    boolean hasErrors = diagnostics.getDiagnostics().stream()
                            .anyMatch(diagnostic -> diagnostic.getKind() == Diagnostic.Kind.ERROR);
                    if (unit == null || hasErrors) {
                        System.out.println(fileRow(i, "UNSUPPORTED_SYNTAX", lines, length));
                        continue;
                    }
                    try {
                        exceedsNodeLimit(unit);
                    } catch (TooManyNodes exception) {
                        System.out.println(fileRow(i, "AST_TOO_LARGE", lines, length));
                        continue;
                    }
                    List<String> rows = new ArrayList<>();
                    String status = "OK";
                    try {
                        SourcePositions sourcePositions = Trees.instance(task).getSourcePositions();
                        if (protocol.equals("PDJ1")) {
                            String packageName = unit.getPackageName() == null
                                    ? "" : typeName(unit.getPackageName());
                            FactScanner scanner = new FactScanner(i, unit, sourcePositions,
                                    rows, packageName, length);
                            if (scanner.omittedAnonymousClassMembers()) {
                                status = "OMITTED_ANONYMOUS_CLASS_MEMBERS";
                            }
                        } else {
                            new AnnotationScanner(i, unit, sourcePositions, rows, length).scan(unit, null);
                        }
                    } catch (TooManyNodes exception) {
                        System.out.println(fileRow(i, "AST_TOO_LARGE", lines, length));
                        continue;
                    } catch (RuntimeException | StackOverflowError exception) {
                        System.out.println(fileRow(i, "PARSER_LIMIT", lines, length));
                        continue;
                    }
                    System.out.println(fileRow(i, status, lines, length));
                    for (String row : rows) System.out.println(row);
                } catch (StackOverflowError exception) {
                    System.out.println(fileRow(i, "PARSER_LIMIT", lines, length));
                }
            }
        } catch (Throwable exception) {
            // Do not print parser diagnostics, paths, excerpts or exception text.
            System.exit(5);
        }
    }
}
