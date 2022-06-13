#!/usr/bin/env python
#
# Copyright (c) 2009 Google Inc. All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#
#    * Redistributions of source code must retain the above copyright
# notice, this list of conditions and the following disclaimer.
#    * Redistributions in binary form must reproduce the above
# copyright notice, this list of conditions and the following disclaimer
# in the documentation and/or other materials provided with the
# distribution.
#    * Neither the name of Google Inc. nor the names of its
# contributors may be used to endorse or promote products derived from
# this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import sre_compile

_regexp_compile_cache = {}


def Match(pattern, s):
    """Matches the string with the pattern, caching the compiled regexp."""
    # The regexp compilation caching is inlined in both Match and Search for
    # performance reasons; factoring it out into a separate function turns out
    # to be noticeably expensive.
    if pattern not in _regexp_compile_cache:
        _regexp_compile_cache[pattern] = sre_compile.compile(pattern)
    return _regexp_compile_cache[pattern].match(s)


def Search(pattern, s):
    """Searches the string for the pattern, caching the compiled regexp."""
    if pattern not in _regexp_compile_cache:
        _regexp_compile_cache[pattern] = sre_compile.compile(pattern)
    return _regexp_compile_cache[pattern].search(s)


def FindEndOfExpressionInLine(line, startpos, stack):
    """Find the position just after the end of current parenthesized expression.

    Args:
      line: a CleansedLines line.
      startpos: start searching at this position.
      stack: nesting stack at startpos.

    Returns:
      On finding matching end: (index just after matching end, None)
      On finding an unclosed expression: (-1, None)
      Otherwise: (-1, new stack at end of this line)
    """
    for i in range(startpos, len(line)):
        char = line[i]
        if char in '([{':
            # Found start of parenthesized expression, push to expression stack
            stack.append(char)
        elif char == '<':
            # Found potential start of template argument list
            if i > 0 and line[i - 1] == '<':
                # Left shift operator
                if stack and stack[-1] == '<':
                    stack.pop()
                    if not stack:
                        return (-1, None)
            elif i > 0 and Search(r'\boperator\s*$', line[0:i]):
                # operator<, don't add to stack
                continue
            else:
                # Tentative start of template argument list
                stack.append('<')
        elif char in ')]}':
            # Found end of parenthesized expression.
            #
            # If we are currently expecting a matching '>', the pending '<'
            # must have been an operator.  Remove them from expression stack.
            while stack and stack[-1] == '<':
                stack.pop()
            if not stack:
                return (-1, None)
            if ((stack[-1] == '(' and char == ')') or
                    (stack[-1] == '[' and char == ']') or
                    (stack[-1] == '{' and char == '}')):
                stack.pop()
                if not stack:
                    return (i + 1, None)
            else:
                # Mismatched parentheses
                return (-1, None)
        elif char == '>':
            # Found potential end of template argument list.

            # Ignore "->" and operator functions
            if (i > 0 and
                    (line[i - 1] == '-' or Search(r'\boperator\s*$', line[0:i - 1]))):
                continue

            # Pop the stack if there is a matching '<'.  Otherwise, ignore
            # this '>' since it must be an operator.
            if stack:
                if stack[-1] == '<':
                    stack.pop()
                    if not stack:
                        return (i + 1, None)
        elif char == ';':
            # Found something that look like end of statements.  If we are currently
            # expecting a '>', the matching '<' must have been an operator, since
            # template argument list should not contain statements.
            while stack and stack[-1] == '<':
                stack.pop()
            if not stack:
                return (-1, None)

    # Did not find end of expression or unbalanced parentheses on this line
    return (-1, stack)


def CloseExpression(clean_lines, linenum, pos):
    """If input points to ( or { or [ or <, finds the position that closes it.

    If lines[linenum][pos] points to a '(' or '{' or '[' or '<', finds the
    linenum/pos that correspond to the closing of the expression.

    TODO(unknown): cpplint spends a fair bit of time matching parentheses.
    Ideally we would want to index all opening and closing parentheses once
    and have CloseExpression be just a simple lookup, but due to preprocessor
    tricks, this is not so easy.

    Args:
      clean_lines: A CleansedLines instance containing the file.
      linenum: The number of the line to check.
      pos: A position on the line.

    Returns:
      A tuple (line, linenum, pos) pointer *past* the closing brace, or
      (line, len(lines), -1) if we never find a close.  Note we ignore
      strings and comments when matching; and the line we return is the
      'cleansed' line at linenum.
    """

    line = clean_lines.elided[linenum]
    if (line[pos] not in '({[<') or Match(r'<[<=]', line[pos:]):
        return (line, clean_lines.NumLines(), -1)

    # Check first line
    (end_pos, stack) = FindEndOfExpressionInLine(line, pos, [])
    if end_pos > -1:
        return (line, linenum, end_pos)

    # Continue scanning forward
    while stack and linenum < clean_lines.NumLines() - 1:
        linenum += 1
        line = clean_lines.elided[linenum]
        (end_pos, stack) = FindEndOfExpressionInLine(line, 0, stack)
        if end_pos > -1:
            return (line, linenum, end_pos)

    # Did not find end of expression before end of file, give up
    return (line, clean_lines.NumLines(), -1)


def ForLoopHelper(stmt):
    buf = stmt.split(';')
    found = False
    ops = ['+', '-', '*', '/', '%', '<<', '>>']

    if len(buf) >= 2:
        for item in ops:
            if item in buf[1]:
                found = True

    return found


def WhileLoopHelper(stmt):
    buf = stmt
    found = False
    ops = ['+', '-', '*', '/', '%', '<<', '>>']

    for item in ops:
        if item in buf:
            found = True

    return found


def CheckLoopCondition(filename, clean_lines, linenum, error):
    """
    Args:
      filename: The name of the current file.
      clean_lines: A CleansedLines instance containing the file.
      linenum: The number of the line to check.
      error: The function to call with any errors found.
    """
    line = clean_lines.elided[linenum]

    matched = Match(r'\s*(for|while)\s*\(', line)
    if matched:
        # Find the end of the conditional expression.
        (end_line, end_linenum, end_pos) = CloseExpression(
            clean_lines, linenum, line.find('('))

        start = line.find('(')
        end = end_pos - 1

        if start >= 0 and end >= 0:
            if matched.group(1) == 'for':
                if ForLoopHelper(line[start+1:end]) is True:
                    error(filename, end_linenum, 'runtime/for_loop_condition', 5,
                          'Possible incorrect condition in range-based for loop')
            elif matched.group(1) == 'while':
                if WhileLoopHelper(line[start+1:end]) is True:
                    error(filename, end_linenum, 'runtime/while_loop_condition', 5,
                          'Possible incorrect condition in range-based while loop')
            else:
                pass
