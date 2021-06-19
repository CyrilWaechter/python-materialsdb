from materialsdb import cache
from materialsdb.ifc import project_library

cache.update_producers_data()
for producer in cache.producers():
    file = project_library.create_project_library_from_xml(str(producer))
    file.write(producer.with_suffix(".ifc").name)
