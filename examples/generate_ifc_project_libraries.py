import os
import platform
import subprocess
from materialsdb import cache, config
from materialsdb.ifc import project_library


def open_folder(path):
    if platform.system() == "Windows":
        os.startfile(path)
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])

def update_cached_library(lang, country):
    config.set_lang(lang)
    config.set_country(country)
    cache.update_producers_data()
    for producer in cache.producers():
        file = project_library.create_project_library_from_xml(str(producer))
        ifc_folder = cache.get_cache_folder() / "IFC4" / f"{lang}_{country}"
        file.write(ifc_folder / producer.with_suffix(".ifc").name)
    open_folder(ifc_folder)

update_cached_library("fr", "CH")