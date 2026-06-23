# Copyright 2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

ROCM_VERSION="6.3"

inherit cmake cuda linux-info rocm

TINY_LLAMAS_COMMIT="99dd1a73db5a37100bd4ae633f4cfce6560e1567"

DESCRIPTION="LLM inference in C/C++"
HOMEPAGE="https://github.com/ggml-org/llama.cpp"

if [[ ${PV} == *9999* ]]; then
	inherit git-r3
	EGIT_REPO_URI="https://github.com/ggml-org/llama.cpp.git"
else
	MY_PV="b${PV#0_pre}"
	SRC_URI="
		https://github.com/ggml-org/llama.cpp/archive/refs/tags/${MY_PV}.tar.gz -> ${P}.tar.gz
		webui? (
			https://github.com/ggml-org/llama.cpp/releases/download/${MY_PV}/llama-${MY_PV}-ui.tar.gz -> ${P}-ui.tar.gz
		)
	"
	S="${WORKDIR}/llama.cpp-${MY_PV}"
	KEYWORDS="~amd64"
fi

SRC_URI+="
	examples? (
		https://huggingface.co/ggml-org/tiny-llamas/resolve/${TINY_LLAMAS_COMMIT}/stories15M-q4_0.gguf
			-> ggml-org_models_tinyllamas_stories15M-q4_0-${TINY_LLAMAS_COMMIT}.gguf
	)
"

LICENSE="MIT"
SLOT="0"

# Optional local CUDA target override for maximum per-machine performance.
# Accepted examples:
#   LLAMA_CPP_CUDA_ARCHITECTURES="auto"
#   LLAMA_CPP_CUDA_ARCHITECTURES="12.0"
#   LLAMA_CPP_CUDA_ARCHITECTURES="120a-real"
#   LLAMA_CPP_CUDA_ARCHITECTURES="8.9;12.0"
#
# When USE="native cuda", set this explicitly for best CUDA performance.
# "auto" requires nvidia-smi to see the build host GPUs from inside Portage's
# build environment, which commonly fails under sandbox/chroot.
LLAMA_CPP_CUDA_ARCHITECTURES="${LLAMA_CPP_CUDA_ARCHITECTURES:-}"

X86_CPU_FLAGS=(
	sse4_2
	avx
	avx_vnni
	avx2
	bmi2
	avx512f avx512cd avx512vl avx512dq avx512bw
	avx512vbmi
	avx512_vnni
	avx512_bf16
	fma3
	f16c
	amx_tile
	amx_int8
	amx_bf16
)
CPU_FLAGS=(
	"${X86_CPU_FLAGS[@]/#/cpu_flags_x86_}"
)

IUSE="
	blis cuda examples flexiblas +lto +native openblas
	opencl +openmp rpc rocm +server vulkan +webui wmma
	+cpu-repack
	${CPU_FLAGS[*]}
"

REQUIRED_USE="
	?? ( openblas blis flexiblas )
	rocm? ( ${ROCM_REQUIRED_USE} )
	wmma? ( rocm )
	webui? ( server )
"

CDEPEND="
	dev-libs/openssl
	openmp? ( llvm-runtimes/openmp:= )
	openblas? ( sci-libs/openblas:= )
	blis? ( sci-libs/blis:= )
	flexiblas? ( sci-libs/flexiblas:= )
	rocm? (
		>=dev-util/hip-${ROCM_VERSION}:=
		>=sci-libs/hipBLAS-${ROCM_VERSION}:=
		wmma? ( >=sci-libs/rocWMMA-${ROCM_VERSION}:= )
	)
	cuda? ( dev-util/nvidia-cuda-toolkit:= )
"
DEPEND="${CDEPEND}
	opencl? ( dev-util/opencl-headers )
	vulkan? ( dev-util/vulkan-headers )
"
RDEPEND="${CDEPEND}
	opencl? ( dev-libs/opencl-icd-loader )
	vulkan? ( media-libs/vulkan-loader )
"
BDEPEND="
	vulkan? ( media-libs/shaderc )
"

pkg_setup() {
	if use rocm; then
		linux-info_pkg_setup
		if linux-info_get_any_version && linux_config_exists; then
			if ! linux_chkconfig_present HSA_AMD_SVM; then
				ewarn "To use ROCm/HIP, enable HSA_AMD_SVM in your kernel."
			fi
		fi
	fi
}

src_unpack() {
	if [[ ${PV} == *9999* ]]; then
		git-r3_src_unpack
	else
		default
	fi

	if use webui; then
		if [[ ${PV} == *9999* ]]; then
			mkdir -p "${S}/tools/ui/dist" || die
			einfo "Downloading webui dist from huggingface bucket..."
			wget -qO - "https://huggingface.co/buckets/ggml-org/llama-ui/resolve/latest/dist.tar.gz" \
				| tar -xzf - -C "${S}/tools/ui/dist" || die
		else
			ln -s "${WORKDIR}/llama-${MY_PV}" "${S}/tools/ui/dist" || die
		fi
	fi
}

src_prepare() {
	use cuda && cuda_src_prepare
	cmake_src_prepare

	if use examples; then
		mkdir -p "${BUILD_DIR}/tinyllamas" || die
		cp "${DISTDIR}/ggml-org_models_tinyllamas_stories15M-q4_0-${TINY_LLAMAS_COMMIT}.gguf" \
			"${BUILD_DIR}/tinyllamas/stories15M-q4_0.gguf" || die
	fi
}

llama_cpp_normalize_cuda_arch_entry() {
	local entry="${1//[[:space:]]/}"

	[[ -n ${entry} ]] || return 0

	case ${entry} in
		*-real|*-virtual)
			printf '%s\n' "${entry}"
			return
			;;
	esac

	if [[ ${entry} == *.* ]]; then
		entry="${entry/./}"
	fi

	if [[ ${entry} =~ ^12[0-9]$ ]]; then
		entry="${entry}a"
	fi

	if [[ ${entry} =~ ^[0-9]+[af]?$ ]]; then
		printf '%s-real\n' "${entry}"
	else
		die "Invalid LLAMA_CPP_CUDA_ARCHITECTURES entry: ${1}"
	fi
}

llama_cpp_normalize_cuda_arches() {
	local input="${1//,/;}"
	input="${input// /;}"

	local -a arch_list=()
	local entry arch
	local IFS=";"

	for entry in ${input}; do
		arch="$(llama_cpp_normalize_cuda_arch_entry "${entry}")"
		[[ -n ${arch} ]] || continue
		if ! has "${arch}" "${arch_list[@]}"; then
			arch_list+=( "${arch}" )
		fi
	done

	[[ ${#arch_list[@]} -gt 0 ]] || die "LLAMA_CPP_CUDA_ARCHITECTURES did not contain any CUDA architectures"
	IFS=";"
	printf '%s\n' "${arch_list[*]}"
}

llama_cpp_detect_cuda_arches() {
	has_version x11-drivers/nvidia-drivers || ewarn "nvidia-drivers is not installed; auto CUDA arch detection may fail."
	command -v nvidia-smi >/dev/null || die "LLAMA_CPP_CUDA_ARCHITECTURES=auto requires nvidia-smi"

	local smi_list caps rc

	smi_list="$(nvidia-smi -L 2>&1)"
	einfo "nvidia-smi -L:"
	einfo "${smi_list}"

	caps="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader,nounits 2>&1)"
	rc=$?
	if [[ ${rc} -ne 0 ]]; then
		eerror "nvidia-smi compute_cap query failed with exit status ${rc}"
		eerror "nvidia-smi output:"
		eerror "${caps}"
		die "Failed to query CUDA compute capability with nvidia-smi"
	fi

	einfo "nvidia-smi compute_cap output: ${caps}"

	caps="${caps//$'\n'/;}"
	llama_cpp_normalize_cuda_arches "${caps}"
}

llama_cpp_die_missing_cuda_arches() {
	eerror "USE=\"native cuda\" needs an explicit CUDA architecture for best local performance."
	eerror "Set LLAMA_CPP_CUDA_ARCHITECTURES to your NVIDIA compute capability."
	eerror ""
	eerror "Find it with:"
	eerror "  nvidia-smi --query-gpu=name,compute_cap --format=csv"
	eerror ""
	eerror "For example, RTX 5070 Ti reports compute_cap 12.0, so use:"
	eerror "  LLAMA_CPP_CUDA_ARCHITECTURES=\"12.0\""
	eerror ""
	eerror "Example /etc/portage/package.env:"
	eerror "  sci-ml/llama-cpp llama-cpp-perf"
	eerror ""
	eerror "Example /etc/portage/env/llama-cpp-perf:"
	eerror "  LLAMA_CPP_CUDA_ARCHITECTURES=\"12.0\""
	eerror ""
	eerror "Advanced: LLAMA_CPP_CUDA_ARCHITECTURES=\"auto\" tries nvidia-smi during emerge,"
	eerror "but it often fails in Portage sandbox/chroot with NVML permission errors."
	die "Missing LLAMA_CPP_CUDA_ARCHITECTURES for native CUDA build"
}

src_configure() {
	if [[ ${PV} == *9999* ]]; then
		local mycmakeargs=(
			-DLLAMA_BUILD_NUMBER="$(git rev-list --count HEAD)"
			-DLLAMA_BUILD_COMMIT="$(git rev-parse HEAD)"
		)
	else
		local mycmakeargs=( -DLLAMA_BUILD_NUMBER="${MY_PV#b}" )
	fi

	mycmakeargs+=(
		-DGGML_BACKEND_DL=OFF
		-DGGML_CCACHE=OFF
		-DGGML_CPU_REPACK="$(usex cpu-repack)"
		-DGGML_CUDA_FA_ALL_QUANTS="$(usex cuda)"
		-DGGML_CUDA_GRAPHS="$(usex cuda)"
		-DGGML_HIP_GRAPHS="$(usex rocm)"
		-DGGML_LTO="$(usex lto)"
		-DGGML_NATIVE="$(usex native)"
		-DCMAKE_SKIP_BUILD_RPATH=ON
		-DLLAMA_BUILD_EXAMPLES="$(usex examples)"
		-DLLAMA_BUILD_SERVER="$(usex server)"
		-DLLAMA_BUILD_TESTS=OFF
		-DLLAMA_BUILD_UI="$(usex webui)"

		-DGGML_CUDA="$(usex cuda)"
		-DGGML_OPENCL="$(usex opencl)"
		-DGGML_OPENMP="$(usex openmp)"
		-DGGML_RPC="$(usex rpc)"
		-DGGML_VULKAN="$(usex vulkan)"
	)

	if ! use native; then
		mycmakeargs+=(
			-DGGML_SSE42="$(usex cpu_flags_x86_sse4_2)"
			-DGGML_AVX="$(usex cpu_flags_x86_avx)"
			-DGGML_AVX_VNNI="$(usex cpu_flags_x86_avx_vnni)"
			-DGGML_AVX2="$(usex cpu_flags_x86_avx2)"
			-DGGML_BMI2="$(usex cpu_flags_x86_bmi2)"
			-DGGML_AVX512_VBMI="$(usex cpu_flags_x86_avx512vbmi)"
			-DGGML_AVX512_VNNI="$(usex cpu_flags_x86_avx512_vnni)"
			-DGGML_AVX512_BF16="$(usex cpu_flags_x86_avx512_bf16)"
			-DGGML_FMA="$(usex cpu_flags_x86_fma3)"
			-DGGML_F16C="$(usex cpu_flags_x86_f16c)"
			-DGGML_AMX_TILE="$(usex cpu_flags_x86_amx_tile)"
			-DGGML_AMX_INT8="$(usex cpu_flags_x86_amx_int8)"
			-DGGML_AMX_BF16="$(usex cpu_flags_x86_amx_bf16)"

		)

		if use cpu_flags_x86_avx512f &&
			use cpu_flags_x86_avx512cd &&
			use cpu_flags_x86_avx512vl &&
			use cpu_flags_x86_avx512dq &&
			use cpu_flags_x86_avx512bw; then
			mycmakeargs+=( -DGGML_AVX512=ON )
		else
			mycmakeargs+=( -DGGML_AVX512=OFF )
		fi
	else
		# Native compilation uses upstream's per-architecture native path
		# instead of Gentoo CPU_FLAGS_*.
		:
	fi

	if use openblas; then
		mycmakeargs+=(
			-DGGML_BLAS=ON
			-DGGML_BLAS_VENDOR=OpenBLAS
		)
	fi

	if use blis; then
		mycmakeargs+=(
			-DGGML_BLAS=ON
			-DGGML_BLAS_VENDOR=FLAME
		)
	fi

	if use flexiblas; then
		mycmakeargs+=(
			-DGGML_BLAS=ON
			-DGGML_BLAS_VENDOR=FlexiBLAS
		)
	fi

	if use cuda; then
		local -x CUDAHOSTCXX="$(cuda_gccdir)"
		local cuda_arches="${LLAMA_CPP_CUDA_ARCHITECTURES}"

		einfo "LLAMA_CPP_CUDA_ARCHITECTURES=${cuda_arches}"

		cuda_add_sandbox
		addpredict "/dev/char/"

		if [[ -z ${cuda_arches} ]] && use native; then
			llama_cpp_die_missing_cuda_arches
		elif [[ ${cuda_arches} == auto ]]; then
			cuda_arches="$(llama_cpp_detect_cuda_arches)"
		elif [[ -n ${cuda_arches} ]]; then
			cuda_arches="$(llama_cpp_normalize_cuda_arches "${cuda_arches}")"
		fi

		if [[ -n ${cuda_arches} ]]; then
			einfo "Using CMAKE_CUDA_ARCHITECTURES=${cuda_arches}"
			mycmakeargs+=( -DCMAKE_CUDA_ARCHITECTURES="${cuda_arches}" )
		fi
	fi

	if use rocm; then
		export HIPCXX="$(hipconfig -l)/clang" HIP_PATH="$(hipconfig -R)"
		mycmakeargs+=(
			-DAMDGPU_TARGETS="$(get_amdgpu_flags)"
			-DGGML_HIP=ON
			-DGGML_HIP_ROCWMMA_FATTN="$(usex wmma)"
		)
	fi

	cmake_src_configure
}
