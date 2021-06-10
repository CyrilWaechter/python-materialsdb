import uuid
from materialsdb import utils
from materialsdb.serialiser import XmlSerialiser
from materialsdb.classes import *

materials = Materials(
    company="MyCompany",
    companyid=Guid(uuid.uuid4()),
    ver=1,
    crd=utils.new_tdatetime(),
    verXML=3,
    material=[],
    sig=THexHash("MySignature", ver=1),
    publickey=THexHash("MyPublicKey", ver=1),
)

material = Material(
    id=Guid(uuid.uuid4()),
    type=Materialtype("simple"),
    information=Information(Names([TCountryLocalizedString("MyMaterial")])),
    layers=Layers([]),
)

layer = Layer(id=Guid(uuid.uuid4()), geometry=[Geometry()], thermal=[], physical=[])

thermal = Thermal(lambda_value=0.04, therm_capa=2.9)  # Warning therm_capa in Wh/(kg.K)

physical = Physical(density=30)

layer.thermal.append(thermal)
layer.physical.append(physical)
material.layers.layer.append(layer)
materials.material.append(material)

XmlSerialiser().to_xml(materials, "create_layers.xml")
