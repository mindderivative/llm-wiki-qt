import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    property string title: qsTr("Pipeline Log")
    required property var logModel

    ListView {
        id: logView
        anchors.fill: parent
        anchors.margins: 4
        clip: true
        model: logModel

        delegate: Label {
            width: ListView.view.width
            text: message
            font.family: "monospace"
            wrapMode: Text.Wrap
        }

        onCountChanged: positionViewAtEnd()
    }

    Label {
        anchors.centerIn: parent
        text: qsTr("No log output yet")
        opacity: 0.6
        visible: logView.count === 0
    }
}
