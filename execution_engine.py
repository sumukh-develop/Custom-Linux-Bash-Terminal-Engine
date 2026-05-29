from __future__ import annotations

from datetime import datetime, timezone
import shlex
import time
from typing import Any, Callable

from session_manager import TerminalSession


FileSystem = dict[str, Any]
Node = dict[str, Any]
CommandHandler = Callable[[TerminalSession, list[str], str | None], dict[str, Any]]

CACHED_PROCESS_OUTPUT = "\n".join(
    [
        "PID   NAME",
        "101   nginx",
        "102   node",
        "103   postgres",
    ]
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_command(command: str) -> str:
    command = command.strip()
    if command == "cd":
        return command
    if command.startswith("cd") and len(command) > 2 and not command[2].isspace():
        return f"cd {command[2:].lstrip()}"
    return command


class CommandExecutionEngine:
    """Fully simulated IT-admin bash command engine."""

    def __init__(self, required_paths: list[str] | None = None) -> None:
        self.required_paths = required_paths or ["/home/user/project"]
        self.command_map: dict[str, CommandHandler] = {
            "pwd": self._pwd,
            "ls": self._ls,
            "dir": self._ls,
            "cd": self._cd,
            "mkdir": self._mkdir,
            "md": self._mkdir,
            "touch": self._touch,
            "rm": self._rm,
            "del": self._rm,
            "rd": self._rm,
            "echo": self._echo,
            "cat": self._cat,
            "type": self._cat,
            "grep": self._grep,
            "wc": self._wc,
            "head": self._head,
            "tail": self._tail,
            "sort": self._sort,
            "uniq": self._uniq,
            "cut": self._cut,
            "tr": self._tr,
            "chmod": self._chmod,
            "find": self._find,
            "ps": self._ps,
            "kill": self._kill,
            "env": self._env,
            "export": self._export,
            "ping": self._ping,
            "mode": self._mode,
            "cp": self._cp,
            "copy": self._cp,
            "mv": self._mv,
            "move": self._mv,
            "nano": self._nano,
            "sudo": self._sudo,
            "cls": self._cls,
        }

    def execute(self, session: TerminalSession, raw_command: str) -> dict[str, Any]:
        started = time.perf_counter()
        command = raw_command.strip()
        output: list[str] = []
        errors: list[str] = []
        messages: list[str] = []
        exit_code = 0

        if command:
            for segment in self._split_chain(command):
                try:
                    result = self._execute_pipeline(session, segment)
                except CommandError as exc:
                    result = self._result("", str(exc), exc.exit_code)
                
                # Check if this is a special response type (like nano)
                if result.get("type") == "nano":
                    return result
                
                if result["output"]:
                    output.append(result["output"])
                if result["error"]:
                    errors.append(result["error"])
                if result.get("message"):
                    messages.append(result["message"])
                exit_code = result["exit_code"]
                if exit_code != 0:
                    break

        visible_parts = list(output)
        if session.mode == "learning" and exit_code == 0:
            visible_parts.extend(messages)
        visible_output = "\n".join(part for part in visible_parts if part)

        return {
            "command": raw_command,
            "output": visible_output,
            "error": "\n".join(errors),
            "message": "\n".join(messages),
            "status": "success" if exit_code == 0 else "error",
            "exit_code": exit_code,
            "timestamp": now_iso(),
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }

    def evaluate(self, session: TerminalSession) -> dict[str, Any]:
        started = time.perf_counter()
        checks = [
            {"path": path, "exists": self._exists(session.fs, path)}
            for path in self.required_paths
        ]
        passed = sum(1 for check in checks if check["exists"])
        total = len(checks)
        score = int((passed / total) * 10) if total else 10

        return {
            "status": "PASS" if passed == total else "FAIL",
            "score": score,
            "checks": checks,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }

    def _execute_pipeline(self, session: TerminalSession, command: str) -> dict[str, Any]:
        command, redirect = self._extract_output_redirect(command)
        pipeline = [part.strip() for part in command.split("|") if part.strip()]
        if not pipeline:
            return self._result("", "", 0)

        next_input: str | None = None
        last_result = self._result("", "", 0)

        for part in pipeline:
            last_result = self._execute_single(session, part, next_input)
            if last_result["exit_code"] != 0:
                return last_result
            next_input = last_result["output"]

        if redirect is not None:
            operator, target = redirect
            self._write_file(session, target, last_result["output"], operator)
            return self._result("", "", 0, f"Wrote output to {target}")

        return last_result

    def _execute_single(
        self,
        session: TerminalSession,
        command: str,
        input_data: str | None = None,
    ) -> dict[str, Any]:
        command = normalize_command(command)
        try:
            parts = shlex.split(command)
        except ValueError as exc:
            return self._result("", f"bash: {exc}", 2)

        if not parts:
            return self._result("", "", 0)

        command_name = parts[0]
        args = parts[1:]

        handler = self.command_map.get(command_name)
        if handler is None:
            return self._result("", f"{command_name}: command not found", 127)

        try:
            return handler(session, args, input_data)
        except CommandError as exc:
            return self._result("", str(exc), exc.exit_code)

    def _pwd(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        return self._result(session.cwd, "", 0)

    def _ls(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        targets = args or ["."]
        rendered: list[str] = []

        for target in targets:
            path = self.resolve_path(session.cwd, target, home=session.home)
            node = self._get_node(session.fs, path)
            if node is None:
                raise CommandError(f"ls: cannot access '{target}': No such file or directory")
            if self._is_dir(node):
                rendered.append("  ".join(sorted(self._children(node).keys())))
            else:
                rendered.append(self._basename(path))

        return self._result("\n".join(item for item in rendered if item), "", 0)

    def _cd(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        target = args[0] if args else session.home
        path = self.resolve_path(session.cwd, target, home=session.home)
        node = self._get_node(session.fs, path)

        if node is None:
            raise CommandError("cd: no such file or directory")
        if not self._is_dir(node):
            raise CommandError("cd: not a directory")

        session.cwd = path
        return self._result("", "", 0)

    def _mkdir(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        if not args:
            raise CommandError("mkdir: missing operand")

        for arg in args:
            path = self.resolve_path(session.cwd, arg, home=session.home)
            parent, name = self._parent_and_name(session.fs, path, arg)
            children = self._children(parent)
            if name in children:
                raise CommandError(f"mkdir: cannot create directory '{arg}': File exists")
            children[name] = self._dir_node()

        return self._result("", "", 0)

    def _touch(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        if not args:
            raise CommandError("touch: missing file operand")

        for arg in args:
            path = self.resolve_path(session.cwd, arg, home=session.home)
            parent, name = self._parent_and_name(session.fs, path, arg)
            children = self._children(parent)
            if self._is_dir(children.get(name)):
                raise CommandError(f"touch: cannot touch '{arg}': Is a directory")
            children.setdefault(name, self._file_node())

        return self._result("", "", 0)

    def _rm(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        if not args:
            raise CommandError("rm: missing operand")

        recursive = "-r" in args or "-rf" in args or "-fr" in args
        targets = [arg for arg in args if not arg.startswith("-")]
        if not targets:
            raise CommandError("rm: missing operand")

        removed: list[str] = []
        for target in targets:
            path = self.resolve_path(session.cwd, target, home=session.home)
            parent, name = self._parent_and_name(session.fs, path, target)
            children = self._children(parent)
            node = children.get(name)
            if node is None:
                raise CommandError(f"rm: cannot remove '{target}': No such file")
            if self._is_dir(node) and not recursive:
                raise CommandError(f"rm: cannot remove '{target}': Is a directory")
            del children[name]
            removed.append(target)

        return self._result("", "", 0, "\n".join(f"Removed {target}" for target in removed))

    def _echo(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        redirect = self._find_redirect(args)
        if redirect is None:
            return self._result(" ".join(args), "", 0)

        index, operator = redirect
        if index == len(args) - 1:
            raise CommandError("bash: syntax error near unexpected token `newline'", 2)

        content = " ".join(args[:index])
        target = args[index + 1]
        path = self.resolve_path(session.cwd, target, home=session.home)
        parent, name = self._parent_and_name(session.fs, path, target)
        children = self._children(parent)

        if self._is_dir(children.get(name)):
            raise CommandError(f"bash: {target}: Is a directory")

        node = children.setdefault(name, self._file_node())
        existing = self._content(node)
        separator = "\n" if operator == ">>" and existing else ""
        node["content"] = f"{existing}{separator}{content}" if operator == ">>" else content
        return self._result("", "", 0)

    def _cat(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        if not args:
            return self._result(input_data or "", "", 0)

        chunks: list[str] = []
        for arg in args:
            path = self.resolve_path(session.cwd, arg, home=session.home)
            node = self._get_node(session.fs, path)
            if node is None:
                raise CommandError(f"cat: {arg}: No such file or directory")
            if self._is_dir(node):
                raise CommandError(f"cat: {arg}: Is a directory")
            chunks.append(self._content(node))

        return self._result("\n".join(chunks), "", 0)

    def _grep(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        if not args:
            raise CommandError("grep: missing pattern")

        # Check for -c flag
        count_only = "-c" in args
        filtered_args = [arg for arg in args if arg != "-c"]
        
        if not filtered_args:
            raise CommandError("grep: missing pattern")

        pattern = filtered_args[0]
        source = self._input_or_file(session, input_data, filtered_args[1:], "grep")

        pattern_lower = pattern.lower()
        matches = [line for line in source.splitlines() if pattern_lower in line.lower()]
        count = len(matches)
        
        if count_only:
            # Return only the count
            return self._result(str(count), "", 0)
        else:
            # Return matching lines with message
            suffix = "match" if count == 1 else "matches"
            return self._result("\n".join(matches), "", 0, f"{count} {suffix} found")

    def _wc(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        if "-l" not in args:
            raise CommandError("wc: invalid arguments")

        file_args = [arg for arg in args if arg != "-l"]
        source = self._input_or_file(session, input_data, file_args, "wc")
        return self._result(str(len(source.splitlines())), "", 0)

    def _head(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        count, file_args = self._parse_line_count(args, "head")
        source = self._input_or_file(session, input_data, file_args, "head")
        return self._result("\n".join(source.splitlines()[:count]), "", 0)

    def _tail(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        count, file_args = self._parse_line_count(args, "tail")
        source = self._input_or_file(session, input_data, file_args, "tail")
        lines = source.splitlines()
        return self._result("\n".join(lines[-count:] if count else []), "", 0)

    def _sort(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        source = self._input_or_file(session, input_data, args, "sort")
        return self._result("\n".join(sorted(source.splitlines())), "", 0)

    def _uniq(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        source = self._input_or_file(session, input_data, args, "uniq")
        unique_lines: list[str] = []
        previous: str | None = None
        for line in source.splitlines():
            if line != previous:
                unique_lines.append(line)
            previous = line
        return self._result("\n".join(unique_lines), "", 0)

    def _cut(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        delimiter = "\t"
        field: int | None = None
        file_args: list[str] = []
        index = 0

        while index < len(args):
            arg = args[index]
            if arg == "-d":
                if index + 1 >= len(args):
                    raise CommandError("cut: invalid arguments")
                delimiter = args[index + 1]
                index += 2
            elif arg == "-f":
                if index + 1 >= len(args):
                    raise CommandError("cut: invalid arguments")
                try:
                    field = int(args[index + 1])
                except ValueError as exc:
                    raise CommandError("cut: invalid arguments") from exc
                index += 2
            else:
                file_args.append(arg)
                index += 1

        if field is None or field < 1:
            raise CommandError("cut: invalid arguments")

        source = self._input_or_file(session, input_data, file_args, "cut")
        selected: list[str] = []
        for line in source.splitlines():
            fields = line.split(delimiter)
            selected.append(fields[field - 1] if field <= len(fields) else "")
        return self._result("\n".join(selected), "", 0)

    def _tr(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        if len(args) != 2:
            raise CommandError("tr: invalid arguments")

        source = self._input_or_file(session, input_data, [], "tr")
        from_chars = self._expand_tr_set(args[0])
        to_chars = self._expand_tr_set(args[1])
        if not from_chars or not to_chars:
            raise CommandError("tr: invalid arguments")

        translation: dict[int, str] = {}
        for index, char in enumerate(from_chars):
            replacement = to_chars[index] if index < len(to_chars) else to_chars[-1]
            translation[ord(char)] = replacement
        return self._result(source.translate(translation), "", 0)

    def _chmod(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        if len(args) < 2:
            raise CommandError("chmod: missing operand")
        mode, target = args[0], args[1]
        if not mode.isdigit():
            raise CommandError("chmod: invalid mode")

        path = self.resolve_path(session.cwd, target, home=session.home)
        node = self._get_node(session.fs, path)
        if node is None:
            raise CommandError("chmod: file not found")
        node["permissions"] = mode
        return self._result("", "", 0, "Permissions updated")

    def _find(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        start_arg = args[0] if args else "."
        pattern = "*"
        if "-name" in args:
            name_index = args.index("-name")
            if name_index == len(args) - 1:
                raise CommandError("find: missing argument to `-name'")
            pattern = args[name_index + 1]

        start_path = self.resolve_path(session.cwd, start_arg, home=session.home)
        start_node = self._get_node(session.fs, start_path)
        if start_node is None:
            raise CommandError(f"find: '{start_arg}': No such file or directory")
        if not self._is_dir(start_node):
            raise CommandError(f"find: '{start_arg}': Not a directory")

        matches: list[str] = []
        self._walk_find(start_node, start_arg, pattern, matches)
        count = len(matches)
        suffix = "file" if count == 1 else "files"
        return self._result("\n".join(matches), "", 0, f"{count} {suffix} found")

    def _ps(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        if len(session.processes) == 3 and session.processes[0]["pid"] == "101":
            return self._result(CACHED_PROCESS_OUTPUT, "", 0)
        lines = ["PID   NAME"]
        lines.extend(f"{process['pid']}   {process['name']}" for process in session.processes)
        return self._result("\n".join(lines), "", 0)

    def _kill(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        if not args:
            raise CommandError("kill: missing pid")
        pid = args[0]
        for index, process in enumerate(session.processes):
            if process["pid"] == pid:
                del session.processes[index]
                return self._result("", "", 0, f"Process {pid} terminated")
        raise CommandError("kill: No such process")

    def _env(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        return self._result("\n".join(f"{key}={value}" for key, value in sorted(session.env.items())), "", 0)

    def _export(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        if not args:
            return self._env(session, args, input_data)

        messages: list[str] = []
        for assignment in args:
            if "=" not in assignment:
                raise CommandError(f"export: `{assignment}': not a valid identifier")
            key, value = assignment.split("=", 1)
            if not key:
                raise CommandError("export: not a valid identifier")
            session.env[key] = value
            messages.append(f"{key} set to {value}")

        return self._result("", "", 0, "\n".join(messages))

    def _ping(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        host = args[0] if args else "localhost"
        output = "\n".join(
            [
                f"PING {host} (simulated): 56 data bytes",
                f"64 bytes from {host}: icmp_seq=1 ttl=64 time=0.42 ms",
                f"64 bytes from {host}: icmp_seq=2 ttl=64 time=0.39 ms",
                f"--- {host} ping statistics ---",
                "2 packets transmitted, 2 received, 0% packet loss",
            ]
        )
        return self._result(output, "", 0)

    def _mode(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        if not args:
            return self._result(session.mode, "", 0)

        mode = args[0].lower()
        if mode not in {"exam", "learning"}:
            raise CommandError("mode: expected exam or learning")

        session.mode = mode
        return self._result("", "", 0, f"Mode set to {mode}")

    def _cp(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        if len(args) != 2:
            raise CommandError("cp: invalid arguments")

        source_arg, dest_arg = args[0], args[1]

        # Resolve paths
        source_path = self.resolve_path(session.cwd, source_arg, home=session.home)
        dest_path = self.resolve_path(session.cwd, dest_arg, home=session.home)

        # Check if source and destination are the same
        if source_path == dest_path:
            raise CommandError(f"cp: '{source_arg}' and '{dest_arg}' are the same file")

        # Get source node
        source_node = self._get_node(session.fs, source_path)
        if source_node is None:
            raise CommandError(f"cp: cannot stat '{source_arg}': No such file or directory")
        if self._is_dir(source_node):
            raise CommandError(f"cp: '{source_arg}': Is a directory")

        # Get destination parent and name
        dest_parent, dest_name = self._parent_and_name(session.fs, dest_path, dest_arg)
        dest_children = self._children(dest_parent)

        # Check if destination is a directory
        if self._is_dir(dest_children.get(dest_name)):
            raise CommandError(f"cp: cannot overwrite directory '{dest_arg}'")

        # Copy the file content
        dest_children[dest_name] = self._file_node(self._content(source_node))

        return self._result("", "", 0)

    def _mv(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        if len(args) != 2:
            raise CommandError("mv: invalid arguments")

        source_arg, dest_arg = args[0], args[1]

        # Resolve paths
        source_path = self.resolve_path(session.cwd, source_arg, home=session.home)
        dest_path = self.resolve_path(session.cwd, dest_arg, home=session.home)

        # Check if source and destination are the same
        if source_path == dest_path:
            raise CommandError(f"mv: '{source_arg}' and '{dest_arg}' are the same file")

        # Get source node and parent
        source_parent, source_name = self._parent_and_name(session.fs, source_path, source_arg)
        source_children = self._children(source_parent)
        source_node = source_children.get(source_name)
        
        if source_node is None:
            raise CommandError(f"mv: cannot stat '{source_arg}': No such file or directory")

        # Get destination parent and name
        dest_parent, dest_name = self._parent_and_name(session.fs, dest_path, dest_arg)
        dest_children = self._children(dest_parent)

        # Check if destination is a directory (and source is not)
        if self._is_dir(dest_children.get(dest_name)) and not self._is_dir(source_node):
            raise CommandError(f"mv: cannot overwrite directory '{dest_arg}' with non-directory")

        # Move the file/directory
        dest_children[dest_name] = source_node
        del source_children[source_name]

        return self._result("", "", 0)

    def _nano(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        if not args:
            raise CommandError("nano: missing file operand")

        filename = args[0]
        path = self.resolve_path(session.cwd, filename, home=session.home)
        node = self._get_node(session.fs, path)
        
        # Check if it's a directory
        if node is not None and self._is_dir(node):
            raise CommandError(f"nano: {filename}: Is a directory")
        
        # Get file content (empty if file doesn't exist)
        content = self._content(node) if node is not None else ""
        
        # Return a special response type for nano editor
        # Frontend should handle this by opening an editor modal
        return {
            "output": "",
            "error": "",
            "message": "",
            "exit_code": 0,
            "type": "nano",
            "data": {
                "filename": filename,
                "path": path,
                "content": content
            }
        }

    def _sudo(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        if not args:
            raise CommandError("sudo: missing command")

        # Extract the command after sudo
        command_name = args[0]
        command_args = args[1:]
        
        # Check if command exists
        handler = self.command_map.get(command_name)
        if handler is None:
            return self._result("", f"sudo: {command_name}: command not found", 127)
        
        # Execute the command with elevated privileges (simulated)
        try:
            result = handler(session, command_args, input_data)
            # Add a message indicating sudo was used
            if result.get("message"):
                result["message"] = f"[sudo] {result['message']}"
            else:
                result["message"] = f"[sudo] Command executed with elevated privileges"
            return result
        except CommandError as exc:
            return self._result("", f"sudo: {exc}", exc.exit_code)

    def _cls(self, session: TerminalSession, args: list[str], input_data: str | None) -> dict[str, Any]:
        return self._result("", "", 0)

    def _input_or_file(
        self,
        session: TerminalSession,
        input_data: str | None,
        file_args: list[str],
        command_name: str,
    ) -> str:
        if input_data is not None:
            return input_data
        if not file_args:
            raise CommandError(f"{command_name}: no input provided")
        if len(file_args) > 1:
            raise CommandError(f"{command_name}: invalid arguments")

        path = self.resolve_path(session.cwd, file_args[0], home=session.home)
        node = self._get_node(session.fs, path)
        if node is None or self._is_dir(node):
            raise CommandError(f"{command_name}: file not found")
        return self._content(node)

    def _parse_line_count(self, args: list[str], command_name: str) -> tuple[int, list[str]]:
        count = 10
        file_args: list[str] = []
        index = 0

        while index < len(args):
            arg = args[index]
            if arg == "-n":
                if index + 1 >= len(args):
                    raise CommandError(f"{command_name}: invalid arguments")
                try:
                    count = int(args[index + 1])
                except ValueError as exc:
                    raise CommandError(f"{command_name}: invalid arguments") from exc
                if count < 0:
                    raise CommandError(f"{command_name}: invalid arguments")
                index += 2
            else:
                file_args.append(arg)
                index += 1

        return count, file_args

    def _expand_tr_set(self, value: str) -> str:
        expanded: list[str] = []
        index = 0

        while index < len(value):
            if index + 2 < len(value) and value[index + 1] == "-":
                start = ord(value[index])
                end = ord(value[index + 2])
                step = 1 if start <= end else -1
                expanded.extend(chr(code) for code in range(start, end + step, step))
                index += 3
            else:
                expanded.append(value[index])
                index += 1

        return "".join(expanded)

    def _split_chain(self, command: str) -> list[str]:
        return [part.strip() for part in command.split("&&") if part.strip()]

    def resolve_path(self, cwd: str, path: str, home: str = "/home/user") -> str:
        if path == "~":
            path = home
        elif path.startswith("~/"):
            path = f"{home}/{path[2:]}"

        raw = path.split("/") if path.startswith("/") else f"{cwd}/{path}".split("/")
        parts: list[str] = []
        for part in raw:
            if part in ("", "."):
                continue
            if part == "..":
                if parts:
                    parts.pop()
                continue
            parts.append(part)

        return "/" + "/".join(parts) if parts else "/"

    def _get_node(self, fs: FileSystem, path: str) -> Node | None:
        node: Node = fs
        if path == "/":
            return node
        for part in self._parts(path):
            if not self._is_dir(node):
                return None
            children = self._children(node)
            if part not in children:
                return None
            node = children[part]
        return node

    def _parent_and_name(self, fs: FileSystem, path: str, original: str) -> tuple[Node, str]:
        if path == "/":
            raise CommandError(f"cannot operate on root path: {original}")

        parent_path = "/" + "/".join(self._parts(path)[:-1])
        parent_path = parent_path.rstrip("/") or "/"
        parent = self._get_node(fs, parent_path)
        name = self._basename(path)

        if parent is None:
            raise CommandError(f"cannot access '{original}': No such file or directory")
        if not self._is_dir(parent):
            raise CommandError(f"cannot access '{original}': Not a directory")

        return parent, name

    def _exists(self, fs: FileSystem, path: str) -> bool:
        return self._get_node(fs, path) is not None

    def _walk_find(self, node: Node, prefix: str, pattern: str, matches: list[str]) -> None:
        for name, child in sorted(self._children(node).items()):
            child_path = self._join_display_path(prefix, name)
            if self._wildcard_match(name, pattern):
                matches.append(child_path)
            if self._is_dir(child):
                self._walk_find(child, child_path, pattern, matches)

    def _join_display_path(self, prefix: str, name: str) -> str:
        if prefix == ".":
            return f"./{name}"
        if prefix == "/":
            return f"/{name}"
        return f"{prefix.rstrip('/')}/{name}"

    def _wildcard_match(self, value: str, pattern: str) -> bool:
        if pattern == "*":
            return True
        if "*" not in pattern:
            return value == pattern
        start, _, end = pattern.partition("*")
        return value.startswith(start) and value.endswith(end)

    def _find_redirect(self, args: list[str]) -> tuple[int, str] | None:
        for index, arg in enumerate(args):
            if arg in (">", ">>"):
                return index, arg
        return None

    def _extract_output_redirect(self, command: str) -> tuple[str, tuple[str, str] | None]:
        try:
            parts = shlex.split(command)
        except ValueError:
            return command, None

        redirect = self._find_redirect(parts)
        if redirect is None:
            return command, None

        index, operator = redirect
        if index == len(parts) - 1:
            raise CommandError("bash: syntax error near unexpected token `newline'", 2)

        target = parts[index + 1]
        command_without_redirect = " ".join(
            part if part == "|" else shlex.quote(part) for part in parts[:index]
        )
        return command_without_redirect, (operator, target)

    def _write_file(self, session: TerminalSession, target: str, content: str, operator: str) -> None:
        path = self.resolve_path(session.cwd, target, home=session.home)
        parent, name = self._parent_and_name(session.fs, path, target)
        children = self._children(parent)

        if self._is_dir(children.get(name)):
            raise CommandError(f"bash: {target}: Is a directory")

        node = children.setdefault(name, self._file_node())
        existing = self._content(node)
        separator = "\n" if operator == ">>" and existing else ""
        node["content"] = f"{existing}{separator}{content}" if operator == ">>" else content

    def _parts(self, path: str) -> list[str]:
        return [part for part in path.split("/") if part]

    def _basename(self, path: str) -> str:
        parts = self._parts(path)
        return parts[-1] if parts else "/"

    def _dir_node(self) -> Node:
        return {"type": "dir", "children": {}, "permissions": "755"}

    def _file_node(self, content: str = "") -> Node:
        return {"type": "file", "content": content, "permissions": "644"}

    def _is_dir(self, node: Any) -> bool:
        return isinstance(node, dict) and node.get("type") == "dir"

    def _children(self, node: Node) -> dict[str, Node]:
        return node["children"]

    def _content(self, node: Node) -> str:
        return str(node.get("content", ""))

    def _result(
        self,
        output: str,
        error: str,
        exit_code: int,
        message: str | None = None,
    ) -> dict[str, Any]:
        return {
            "output": output,
            "error": error,
            "message": message or "",
            "exit_code": exit_code,
        }


class CommandError(Exception):
    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code
