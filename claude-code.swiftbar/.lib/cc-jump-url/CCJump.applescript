-- CCJump — minimal URL-scheme handler that hands a claude-code-swiftbar://…
-- URL off to the sibling .bin/cc-jump shell script.
--
-- Built into a .app bundle by install.sh via `osacompile` so LaunchServices
-- can register `claude-code-swiftbar://` to it. The .app sits inside the
-- SwiftBar plugin bundle as .bin/CCJump.app, side-by-side with .bin/cc-jump
-- — that way the handler finds cc-jump via a fixed relative path regardless
-- of where the bundle is installed.
--
-- URL shape: claude-code-swiftbar://jump?sid=<session_id>&cwd=<percent-encoded-cwd>
-- - sid is optional (cc-jump tolerates empty)
-- - cwd is REQUIRED (cc-jump's only mandatory arg)
--
-- Invisible (LSUIElement=true via post-build plist edit). Clicking a
-- notification with href=claude-code-swiftbar://… launches us, we fire
-- cc-jump, and we exit immediately.

on open location this_url
	try
		set sid to my queryParam(this_url, "sid")
		set cwdRaw to my queryParam(this_url, "cwd")
		if cwdRaw is "" then return
		set cwd to my percentDecode(cwdRaw)
		-- We live at .bin/CCJump.app/Contents/MacOS/applet — cc-jump is at
		-- .bin/cc-jump, i.e. ../../../cc-jump from POSIX path of (path to me).
		-- (path to me) returns the .app bundle path, so it's just ../cc-jump.
		set myAppPath to POSIX path of (path to me)
		set jumpPath to myAppPath & "../cc-jump"
		do shell script "/bin/bash " & quoted form of jumpPath & " " & quoted form of sid & " " & quoted form of cwd & " >/dev/null 2>&1 &"
	end try
end open location

on run
	-- Launched without a URL (e.g. user double-clicked the .app). We exist
	-- solely as a URL handler; do nothing.
	return
end run

-- Find &key=value in URL, return value (still percent-encoded), "" if absent.
on queryParam(theUrl, theKey)
	set qPos to offset of "?" in theUrl
	if qPos is 0 then return ""
	set qs to text (qPos + 1) thru -1 of theUrl
	set AppleScript's text item delimiters to "&"
	set parts to text items of qs
	set AppleScript's text item delimiters to ""
	repeat with p in parts
		set pStr to p as text
		set ePos to offset of "=" in pStr
		if ePos > 0 then
			set k to text 1 thru (ePos - 1) of pStr
			set v to text (ePos + 1) thru -1 of pStr
			if k is theKey then return v
		end if
	end repeat
	return ""
end queryParam

-- Percent-decode using Python (always present on macOS). Avoids handcrafting
-- UTF-8 byte handling in pure AppleScript.
on percentDecode(s)
	if s is "" then return ""
	try
		return do shell script "/usr/bin/python3 -c 'import sys, urllib.parse; sys.stdout.write(urllib.parse.unquote(sys.argv[1]))' " & quoted form of s
	on error
		return s
	end try
end percentDecode
