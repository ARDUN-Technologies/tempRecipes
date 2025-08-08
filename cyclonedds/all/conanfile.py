from conan import ConanFile
from conan.tools.build import check_min_cppstd
from conan.tools.cmake import (
    CMakeToolchain,
    CMakeDeps,
    CMake,
    cmake_layout
)
from conan.tools.files import (
    get,
    apply_conandata_patches,
    copy,
    export_conandata_patches,
    rmdir,
    rm
)
from conan.tools.scm import Version
import os

required_conan_version = ">=2.0"

class CycloneDDSConan(ConanFile):
    name = "cyclonedds"
    license = "EPL-2.0"
    url = "https://github.com/conan-io/conan-center-index"
    homepage = "https://cyclonedds.io/"
    description = (
        "Eclipse Cyclone DDS - An implementation of the "
        "OMG Data Distribution Service (DDS) specification"
    )
    topics = ("dds", "ipc", "ros", "middleware")

    package_type = "library"

    settings = "os", "arch", "compiler", "build_type"
    options = {
        "shared":          [True, False],
        "fPIC":            [True, False],
        "with_ssl":        [True, False],
        "with_shm":        [True, False],
        "enable_security": [True, False],
        "enable_discovery":[True, False],
    }
    default_options = {
        "shared":          False,
        "fPIC":            True,
        "with_ssl":        False,
        "with_shm":        False,
        "enable_security": False,
        "enable_discovery":True,
    }

    short_paths = True

    @property
    def _min_cppstd(self):
        return "14"

    @property
    def _compilers_minimum_version(self):
        return {
            "gcc":         "7",
            "clang":       "7",
            "apple-clang": "10",
            "msvc":        "192",
            "Visual Studio": "16",
        }

    def export_sources(self):
        # Keep patches + custom cmake snippet for idlc
        export_conandata_patches(self)

    def config_options(self):
        # no fPIC on Windows
        if self.settings.os == "Windows":
            del self.options.fPIC

    def configure(self):
        # fPIC only matters for static builds
        if self.options.shared:
            del self.options.fPIC
        # Conan 2 doesn’t need to track these
        self.settings.rm_safe("compiler.libcxx")
        self.settings.rm_safe("compiler.cppstd")

    def layout(self):
        cmake_layout(self, src_folder="src")

    def requirements(self):
        if self.options.with_shm:
            self.requires("iceoryx/2.0.5")
        if self.options.with_ssl:
            self.requires("openssl/[>=1.1 <4]")

    def validate(self):
        # static + security is not supported upstream
        if self.options.enable_security and not self.options.shared:
            raise ConanInvalidConfiguration(
                "CycloneDDS: security only supported in shared builds"
            )
        # check C++ std
        if self.settings.compiler.get_safe("cppstd"):
            check_min_cppstd(self, self._min_cppstd)
        # check compiler version
        min_version = self._compilers_minimum_version.get(str(self.settings.compiler), None)
        if min_version and Version(self.settings.compiler.version) < min_version:
            raise ConanInvalidConfiguration(
                f"CycloneDDS requires at least C++{self._min_cppstd}; "
                f"{self.settings.compiler} {self.settings.compiler.version} is too old."
            )

    def build_requirements(self):
        self.tool_requires("cmake/[>=3.16 <4]")

    def source(self):
        get(self, **self.conan_data["sources"][self.version], strip_root=True)
        apply_conandata_patches(self)

    def generate(self):
        tc = CMakeToolchain(self)
        # control which sub‐projects to build
        tc.variables["BUILD_IDLC"]         = self._has_idlc()
        tc.variables["BUILD_IDLC_TESTING"] = False
        tc.variables["BUILD_DDSPERF"]      = False
        tc.variables["ENABLE_SSL"]         = self.options.with_ssl
        tc.variables["ENABLE_SHM"]         = self.options.with_shm
        tc.variables["ENABLE_SECURITY"]    = self.options.enable_security
        tc.variables["ENABLE_TYPE_DISCOVERY"]  = self.options.enable_discovery
        tc.variables["ENABLE_TOPIC_DISCOVERY"] = self.options.enable_discovery
        # disable LTO, examples, tests
        tc.cache_variables["ENABLE_LTO"] = False
        tc.generate()

        cd = CMakeDeps(self)
        cd.generate()

    def build(self):
        cmake = CMake(self)
        cmake.configure()
        cmake.build()

    def package(self):
        cmake = CMake(self)
        cmake.install()

        # licenses
        copy(self, "LICENSE", src=self.source_folder, dst=os.path.join(self.package_folder, "licenses"))

        # clean up
        rmdir(self, os.path.join(self.package_folder, "share"))
        rmdir(self, os.path.join(self.package_folder, "lib", "pkgconfig"))
        # remove upstream cmake glue we don’t want
        rm(self, "*.cmake", os.path.join(self.package_folder, "lib", "cmake", "CycloneDDS"))

        if self.settings.os == "Windows":
            # strip out MSVC runtime DLLs + PDBs
            for pattern in ("*.pdb", "concrt*.dll", "msvcp*.dll", "vcruntime*.dll"):
                rm(self, pattern, os.path.join(self.package_folder, "bin"))

    def package_info(self):
        # Global pkg-config / cmake config
        self.cpp_info.set_property("pkg_config_name", "CycloneDDS")
        self.cpp_info.set_property("cmake_file_name",  "CycloneDDS")

        # ----- Component: ddsc (core library) -----
        ddsc = self.cpp_info.components["ddsc"]
        ddsc.set_property("cmake_target_name", "CycloneDDS::ddsc")
        ddsc.libs = ["ddsc"]
        # optional deps
        if self.options.with_shm:
            ddsc.requires.append("iceoryx::iceoryx_binding_c")
        if self.options.with_ssl:
            ddsc.requires.append("openssl::openssl")
        # system libs
        if self.settings.os in ("Linux", "FreeBSD"):
            ddsc.system_libs = ["dl", "pthread"]
        elif self.settings.os == "Windows":
            ddsc.system_libs = ["ws2_32", "dbghelp", "bcrypt", "iphlpapi"]

        # ----- Component: idl (IDL compiler helper) -----
        if self._has_idlc():
            idl = self.cpp_info.components["idl"]
            idl.set_property("cmake_target_name", "CycloneDDS::idl")
            idl.libs = ["cycloneddsidl"]

            # Use the CCI-shipped helper, no custom copy needed
            idl.set_property("cmake_build_modules", [
                os.path.join("lib", "cmake", "CycloneDDS", "CycloneDDS_idlc.cmake"),
                os.path.join("lib", "cmake", "CycloneDDS", "idlc", "Generate.cmake"),
            ])

            bin_path = os.path.join(self.package_folder, "bin")
            self.buildenv_info.prepend_path("PATH", bin_path)
            self.runenv_info.prepend_path("PATH", bin_path)
