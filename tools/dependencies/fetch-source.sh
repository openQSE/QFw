# Sourced by the dependency builders.

# fetch_source URL REF DEST
#
# Check out revision REF of the git repository at URL into the new directory
# DEST, with its submodules, recursively, at the commits REF records. When
# REF is a full commit id, a branch or a tag, only the files of that
# revision are fetched, not the history. Any other revision, such as an
# abbreviated commit id, needs the history to resolve: the repository is then
# cloned in full and REF checked out.
fetch_source() {
	local url="$1"
	local ref="$2"
	local dest="$3"

	git init --quiet "${dest}"
	git -C "${dest}" remote add origin "${url}"
	if git -C "${dest}" fetch --depth 1 origin "${ref}"; then
		git -C "${dest}" -c advice.detachedHead=false \
			checkout --detach FETCH_HEAD
		git -C "${dest}" submodule update --init --recursive --depth 1
		return
	fi

	printf '%s: %s is not a full commit id, branch or tag of %s;' \
		"${0##*/}" "${ref}" "${url}" >&2
	printf ' cloning its whole history\n' >&2
	rm -rf "${dest}"
	git clone --recursive "${url}" "${dest}"
	git -C "${dest}" checkout --detach "${ref}"
	git -C "${dest}" submodule update --init --recursive
}
